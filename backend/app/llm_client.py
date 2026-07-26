"""The only module in the codebase that talks to an LLM provider.

Everything else — agents, voice parsing — goes through `LLMClient.generate_structured`.
Swapping provider or adding a paid key is therefore a change to this one file.

Two concerns live here, and they are deliberately separate from the
JSON-validation retry in the callers:

**Pacing.** The Gemini free tier is limited per *minute* (historically ~15 RPM
on flash models). A negotiation round fires one call per persona, so firing
them concurrently is the fastest way to a 429. Every call takes a semaphore of
one and waits until at least `llm_min_interval_seconds` has passed since the
last one started, which turns a burst into a queue.

**Backoff.** 429 and 5xx are retried with exponential backoff (2s, 4s, ...).
That is transport-level recovery; a *well-formed response that fails schema
validation* is a different problem and is retried by the caller with the error
fed back to the model.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, TypeVar

from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel

from app.config import Settings

logger = logging.getLogger("boardroom.llm")

__all__ = [
    "LLMError",
    "LLMClient",
    "ToolCallRequest",
    "build_llm_client",
    "to_gemini_schema",
]

#: Status codes worth waiting out rather than giving up on.
RETRYABLE_STATUS = {429, 500, 502, 503, 504}

T = TypeVar("T")


class LLMError(RuntimeError):
    """Any failure to get a usable response out of the provider."""


@dataclass(frozen=True)
class ToolCallRequest:
    """A tool the model asked for, with the arguments it chose."""

    name: str
    args: dict[str, Any]


#: Keys Pydantic emits that Gemini's `response_schema` proto has no field for.
#: `additionalProperties` is the one that actually breaks a request — it comes
#: from `extra="forbid"`, which every agent model sets, and the API rejects the
#: whole call with a 400. The rest are dropped as noise.
_UNSUPPORTED_SCHEMA_KEYS = frozenset(
    {"additionalProperties", "title", "default", "$defs", "discriminator"}
)


def to_gemini_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Pydantic model -> a schema dict Gemini's JSON mode will accept.

    Three reductions, each because the API rejects or ignores the input:

    * `additionalProperties` is stripped. `extra="forbid"` is right for
      *validating* a response and meaningless as an instruction to the model,
      but the SDK forwards it and the request 400s.
    * `$ref`/`$defs` are inlined. Nested models produce references; Gemini's
      schema has no concept of them.
    * `anyOf: [X, null]` becomes X plus `nullable`, which is how Gemini spells
      an optional field.

    Passing a dict rather than the class also keeps the model strict: callers
    still validate the response against the real Pydantic model afterwards.
    """
    raw = model.model_json_schema()
    defs: dict[str, Any] = raw.pop("$defs", {})

    def reduce(node: Any) -> Any:
        if isinstance(node, list):
            return [reduce(item) for item in node]
        if not isinstance(node, dict):
            return node

        if "$ref" in node:
            name = str(node["$ref"]).rsplit("/", 1)[-1]
            return reduce(deepcopy(defs.get(name, {})))

        # `X | None` -> the non-null branch, marked nullable.
        if "anyOf" in node:
            branches = [b for b in node["anyOf"] if b.get("type") != "null"]
            nullable = len(branches) != len(node["anyOf"])
            if len(branches) == 1:
                merged = {
                    **{k: v for k, v in node.items() if k != "anyOf"},
                    **branches[0],
                }
                reduced = reduce(merged)
                if nullable:
                    reduced["nullable"] = True
                return reduced

        return {
            key: reduce(value)
            for key, value in node.items()
            if key not in _UNSUPPORTED_SCHEMA_KEYS
        }

    return reduce(raw)  # type: ignore[return-value]


def _interpret_json(response: Any) -> dict[str, Any]:
    """JSON-mode response -> parsed object."""
    text = (response.text or "").strip()
    if not text:
        raise LLMError("provider returned an empty response")
    return json.loads(text)


def _interpret_tool_call(response: Any) -> ToolCallRequest | None:
    """Tool-mode response -> the first requested call, or None.

    Only the first call is taken: agents are capped at one search per turn, so
    a model that asks for several gets the first honoured and the rest dropped
    rather than fanning out into an unbounded chain.

    An empty response is *not* an error here — a turn where the model neither
    calls a tool nor says anything simply means "no search", and the caller
    proceeds to the structured call regardless.
    """
    calls = getattr(response, "function_calls", None) or []
    for call in calls:
        name = getattr(call, "name", None)
        if not name:
            continue
        args = getattr(call, "args", None)
        return ToolCallRequest(name=name, args=dict(args) if args else {})
    return None


class LLMClient:
    """Serialized, rate-limit-aware access to Gemini with structured output."""

    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        self._settings = settings
        self._client = client or genai.Client(api_key=settings.gemini_api_key)
        # Concurrency of exactly one: the free tier punishes parallelism.
        #
        # THIS QUEUE IS GLOBAL ACROSS EVERY SESSION, DELIBERATELY. One
        # LLMClient is built in `main.py` and shared by all of them, so an
        # agent turn in session A waits behind an agent turn in session B.
        #
        # Do not "fix" this by giving each session its own client or semaphore.
        # The limit being protected is a per-API-key quota, not a per-session
        # one; N private queues would fire N concurrent calls against the same
        # key and multiply the 429 rate by N. The visible consequence — rounds
        # getting slower as more sessions run — is the rate limit working, not
        # a bug.
        self._gate = asyncio.Semaphore(1)
        self._last_call_started = 0.0

    # ------------------------------------------------------------------ #

    async def _throttle(self) -> None:
        """Hold until the minimum gap since the previous call has elapsed."""
        gap = self._settings.llm_min_interval_seconds
        if gap <= 0:
            return
        elapsed = time.monotonic() - self._last_call_started
        if elapsed < gap:
            await asyncio.sleep(gap - elapsed)

    @staticmethod
    def _status_of(exc: Exception) -> int | None:
        code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
        return code if isinstance(code, int) else None

    async def generate_structured(
        self,
        prompt: str,
        schema: type[BaseModel],
        *,
        system: str | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        """Return one JSON object matching `schema`.

        `model` overrides the configured one for this call. Extraction work —
        the scribe, the voice offer parser — is not reasoning work, and routing
        it to a smaller model keeps it from competing with the negotiators for
        the same per-minute budget. It still shares this client's queue, because
        the quota being protected is per key, not per model.

        Uses Gemini's native JSON mode (`response_mime_type` +
        `response_schema`) rather than asking for JSON in the prompt — the
        model is constrained rather than trusted. Callers still validate, since
        a schema-shaped response can still be semantically wrong.

        Raises `LLMError` if every attempt fails.
        """
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            # A reduced dict, not the class: see `to_gemini_schema`.
            response_schema=to_gemini_schema(schema),
            max_output_tokens=self._settings.gemini_max_output_tokens,
            **({"system_instruction": system} if system else {}),
        )
        return await self._call(prompt, config, _interpret_json, model=model)

    async def generate_with_tools(
        self,
        prompt: str,
        tools: list[types.Tool],
        *,
        system: str | None = None,
    ) -> ToolCallRequest | None:
        """Offer `tools` and report back whether the model wants to use one.

        Deliberately *not* combined with JSON mode. Gemini rejected
        `response_schema` alongside function declarations outright until the
        Gemini 3 series, where the combination is still preview and documented
        only on a different API surface than `generate_content`. Callers
        therefore run this first to decide whether to search, then make a
        separate `generate_structured` call for the actual decision — which
        keeps the structured path identical to a no-tools turn.

        Returns the requested call, or `None` if the model answered directly
        (its prose is discarded; only the tool decision matters here).

        Shares the semaphore, throttle and backoff with every other call — a
        tool-using turn is two paced calls, not one call plus a free one.
        """
        config = types.GenerateContentConfig(
            tools=tools,
            max_output_tokens=self._settings.gemini_max_output_tokens,
            **({"system_instruction": system} if system else {}),
        )
        return await self._call(prompt, config, _interpret_tool_call)

    async def _call(
        self,
        prompt: str,
        config: types.GenerateContentConfig,
        interpret: Callable[[Any], T],
        *,
        model: str | None = None,
    ) -> T:
        """One paced, retried, timed-out call. `interpret` reads the response.

        `interpret` raising `LLMError` is treated as a retryable provider
        failure, which is how an empty response earns another attempt.
        """
        attempts = max(1, self._settings.llm_max_attempts)
        last_error: Exception | None = None
        #: What was actually spent, which is not `attempts` when a call fails on
        #: something non-retryable. Reporting the cap instead reads as "we tried
        #: three times and the provider is flaky" for what was one hard 404.
        made = 0

        for attempt in range(1, attempts + 1):
            made = attempt
            async with self._gate:
                await self._throttle()
                self._last_call_started = time.monotonic()
                try:
                    response = await asyncio.wait_for(
                        self._client.aio.models.generate_content(
                            model=model or self._settings.gemini_model,
                            contents=prompt,
                            config=config,
                        ),
                        timeout=self._settings.llm_timeout_seconds,
                    )
                    return interpret(response)

                except json.JSONDecodeError as exc:
                    # JSON mode should make this impossible; treat as terminal
                    # so the caller's own retry (with the error fed back) runs.
                    raise LLMError(f"provider returned non-JSON: {exc}") from exc

                except (genai_errors.APIError, asyncio.TimeoutError, LLMError) as exc:
                    last_error = exc
                    status = self._status_of(exc)
                    retryable = isinstance(exc, asyncio.TimeoutError) or (
                        status in RETRYABLE_STATUS if status is not None else True
                    )
                    if not retryable or attempt == attempts:
                        break
                    wait = self._settings.llm_backoff_base_seconds * (2 ** (attempt - 1))
                    logger.warning(
                        "gemini call failed (attempt %d/%d, status=%s): %s — retrying in %.1fs",
                        attempt,
                        attempts,
                        status,
                        exc,
                        wait,
                    )

            # Sleep outside the semaphore so a backing-off call doesn't block
            # the queue any longer than the throttle already requires.
            await asyncio.sleep(wait)

        raise LLMError(f"gemini call failed after {made} attempt(s): {last_error}")


def build_llm_client(settings: Settings) -> LLMClient:
    return LLMClient(settings)
