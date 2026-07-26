"""The Gemini wrapper: pacing, backoff, and structured-output plumbing.

These are the behaviours that keep a live demo off the free tier's 429 wall,
so they're tested directly rather than inferred. No network: a fake stands in
for the SDK client at the `aio.models.generate_content` seam.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest
from google.genai import errors as genai_errors
from pydantic import BaseModel

from app.config import Settings
from app.llm_client import LLMClient, LLMError, ToolCallRequest, to_gemini_schema
from app.search import WEB_SEARCH_TOOL


class Sample(BaseModel):
    value: str


def make_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "gemini_api_key": "fake",
        "gemini_model": "gemini-3.6-flash",
        "llm_min_interval_seconds": 0.0,
        "llm_max_attempts": 3,
        "llm_backoff_base_seconds": 0.01,
        "llm_timeout_seconds": 5.0,
        "_env_file": None,
    }
    base.update(overrides)
    return Settings(**base)


class FakeAPIError(genai_errors.APIError):
    """An SDK error with a chosen status, without invoking the real ctor."""

    def __init__(self, code: int, message: str = "boom") -> None:
        self.code = code
        self.message = message
        Exception.__init__(self, message)


class FakeFunctionCall:
    """A tool call the model asked for. Use as an outcome to script one."""

    def __init__(self, name: str, args: dict[str, Any] | None = None) -> None:
        self.name = name
        self.args = args or {}


class FakeResponse:
    def __init__(self, text: str, function_calls: list[FakeFunctionCall] | None = None) -> None:
        self.text = text
        # The real SDK exposes this convenience property; it's empty on a
        # plain text answer, which is exactly the "model declined to search" case.
        self.function_calls = function_calls or []


class FakeModels:
    def __init__(self, outcomes: list[Any]) -> None:
        self._outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []
        self.started_at: list[float] = []
        self.concurrent = 0
        self.max_concurrent = 0

    async def generate_content(self, *, model: str, contents: Any, config: Any) -> FakeResponse:
        self.concurrent += 1
        self.max_concurrent = max(self.max_concurrent, self.concurrent)
        self.started_at.append(time.monotonic())
        self.calls.append({"model": model, "contents": contents, "config": config})
        try:
            await asyncio.sleep(0)
            if not self._outcomes:
                raise AssertionError("fake ran out of outcomes")
            item = self._outcomes.pop(0)
            if isinstance(item, Exception):
                raise item
            if isinstance(item, float):  # a slow call, for timeout tests
                await asyncio.sleep(item)
                return FakeResponse('{"value": "slow"}')
            if isinstance(item, FakeFunctionCall):
                return FakeResponse("", function_calls=[item])
            if isinstance(item, list):  # several calls in one response
                return FakeResponse("", function_calls=item)
            return FakeResponse(item)
        finally:
            self.concurrent -= 1


class FakeSDK:
    def __init__(self, *outcomes: Any) -> None:
        self.models = FakeModels(list(outcomes))
        self.aio = self

    # `aio.models` resolves back to the same FakeModels via self-reference.


def build(*outcomes: Any, **settings_overrides: Any) -> tuple[LLMClient, FakeModels]:
    sdk = FakeSDK(*outcomes)
    client = LLMClient(make_settings(**settings_overrides), client=sdk)
    return client, sdk.models


# --------------------------------------------------------------------------- #
# Structured output
# --------------------------------------------------------------------------- #


async def test_a_valid_response_is_parsed_into_a_dict() -> None:
    client, models = build('{"value": "ok"}')

    result = await client.generate_structured("hello", Sample)

    assert result == {"value": "ok"}
    assert len(models.calls) == 1


async def test_the_request_uses_gemini_json_mode_with_the_schema() -> None:
    """Constrain the model rather than asking it nicely for JSON."""
    client, models = build('{"value": "ok"}')

    await client.generate_structured("hello", Sample, system="be terse")
    config = models.calls[0]["config"]

    assert config.response_mime_type == "application/json"
    # A reduced dict, not the class: the class carries `additionalProperties`
    # from `extra="forbid"`, which Gemini rejects with a 400. See
    # `to_gemini_schema` and the schema-reduction tests below.
    assert config.response_schema == to_gemini_schema(Sample)
    assert config.system_instruction == "be terse"
    assert models.calls[0]["model"] == "gemini-3.6-flash"


async def test_the_system_instruction_is_omitted_when_not_given() -> None:
    client, models = build('{"value": "ok"}')

    await client.generate_structured("hello", Sample)

    assert models.calls[0]["config"].system_instruction is None


async def test_non_json_text_is_a_terminal_error_not_a_retry() -> None:
    """JSON mode should prevent this; if it happens the caller must decide."""
    client, models = build("this is not json")

    with pytest.raises(LLMError, match="non-JSON"):
        await client.generate_structured("hello", Sample)

    assert len(models.calls) == 1


async def test_an_empty_response_is_retried() -> None:
    client, models = build("", '{"value": "recovered"}')

    result = await client.generate_structured("hello", Sample)

    assert result == {"value": "recovered"}
    assert len(models.calls) == 2


# --------------------------------------------------------------------------- #
# Rate limiting
# --------------------------------------------------------------------------- #


async def test_a_429_is_retried_with_backoff_and_can_succeed() -> None:
    client, models = build(FakeAPIError(429), '{"value": "after backoff"}')

    result = await client.generate_structured("hello", Sample)

    assert result == {"value": "after backoff"}
    assert len(models.calls) == 2


async def test_backoff_grows_between_attempts() -> None:
    client, models = build(
        FakeAPIError(429),
        FakeAPIError(429),
        '{"value": "third time"}',
        llm_backoff_base_seconds=0.05,
    )

    await client.generate_structured("hello", Sample)

    first_gap = models.started_at[1] - models.started_at[0]
    second_gap = models.started_at[2] - models.started_at[1]
    assert first_gap == pytest.approx(0.05, abs=0.04)
    assert second_gap > first_gap  # 0.05 -> 0.10


async def test_attempts_are_capped_and_then_raise() -> None:
    client, models = build(
        FakeAPIError(429), FakeAPIError(429), FakeAPIError(429), llm_max_attempts=3
    )

    with pytest.raises(LLMError, match="after 3 attempt"):
        await client.generate_structured("hello", Sample)

    assert len(models.calls) == 3


@pytest.mark.parametrize("status", [500, 503])
async def test_server_errors_are_retried_too(status: int) -> None:
    client, models = build(FakeAPIError(status), '{"value": "ok"}')

    assert await client.generate_structured("hello", Sample) == {"value": "ok"}
    assert len(models.calls) == 2


@pytest.mark.parametrize("status", [400, 403, 404])
async def test_client_errors_are_not_retried(status: int) -> None:
    """A bad request won't fix itself; retrying just burns quota."""
    client, models = build(FakeAPIError(status), '{"value": "unused"}')

    with pytest.raises(LLMError):
        await client.generate_structured("hello", Sample)

    assert len(models.calls) == 1


async def test_a_timeout_is_retried() -> None:
    client, models = build(5.0, '{"value": "ok"}', llm_timeout_seconds=0.05)

    assert await client.generate_structured("hello", Sample) == {"value": "ok"}
    assert len(models.calls) == 2


# --------------------------------------------------------------------------- #
# Pacing — the thing that keeps a round under the free tier's RPM budget
# --------------------------------------------------------------------------- #


async def test_calls_are_serialized_never_concurrent() -> None:
    """Three personas acting at once is the fastest route to a 429."""
    client, models = build(*['{"value": "ok"}'] * 3)

    await asyncio.gather(
        *(client.generate_structured(f"p{i}", Sample) for i in range(3))
    )

    assert len(models.calls) == 3
    assert models.max_concurrent == 1


async def test_consecutive_calls_are_spaced_by_the_minimum_interval() -> None:
    client, models = build(*['{"value": "ok"}'] * 3, llm_min_interval_seconds=0.08)

    for i in range(3):
        await client.generate_structured(f"p{i}", Sample)

    gaps = [
        models.started_at[i + 1] - models.started_at[i]
        for i in range(len(models.started_at) - 1)
    ]
    assert all(gap >= 0.07 for gap in gaps), gaps


async def test_a_zero_interval_disables_throttling() -> None:
    """Tests and paid keys shouldn't pay the free-tier tax."""
    client, models = build(*['{"value": "ok"}'] * 3, llm_min_interval_seconds=0.0)

    start = time.monotonic()
    for i in range(3):
        await client.generate_structured(f"p{i}", Sample)

    assert time.monotonic() - start < 0.5
    assert len(models.calls) == 3


# --------------------------------------------------------------------------- #
# Tool calling
#
# Phase one of a search-enabled turn: offer the tool, find out whether the model
# wants it. Deliberately separate from JSON mode — see `generate_with_tools`.
# --------------------------------------------------------------------------- #


async def test_a_requested_tool_call_is_returned() -> None:
    client, models = build(FakeFunctionCall("web_search", {"query": "copper price"}))

    call = await client.generate_with_tools("hello", [WEB_SEARCH_TOOL])

    assert call == ToolCallRequest(name="web_search", args={"query": "copper price"})
    assert models.calls[0]["config"].tools == [WEB_SEARCH_TOOL]


async def test_a_plain_text_answer_means_no_tool_wanted() -> None:
    """The prose is discarded; only the search decision matters in phase one."""
    client, _ = build("I don't need to look anything up.")

    assert await client.generate_with_tools("hello", [WEB_SEARCH_TOOL]) is None


async def test_an_empty_response_is_not_an_error_in_tool_mode() -> None:
    """Unlike JSON mode, silence here just means 'no search'."""
    client, models = build("")

    assert await client.generate_with_tools("hello", [WEB_SEARCH_TOOL]) is None
    assert len(models.calls) == 1  # not retried


async def test_only_the_first_of_several_tool_calls_is_taken() -> None:
    """Agents are capped at one search per turn; extra calls are dropped."""
    client, _ = build(
        [
            FakeFunctionCall("web_search", {"query": "first"}),
            FakeFunctionCall("web_search", {"query": "second"}),
        ]
    )

    call = await client.generate_with_tools("hello", [WEB_SEARCH_TOOL])

    assert call is not None and call.args == {"query": "first"}


async def test_tool_mode_does_not_request_json_output() -> None:
    """Sending response_schema alongside tools is what the two-phase design avoids."""
    client, models = build(FakeFunctionCall("web_search", {"query": "q"}))

    await client.generate_with_tools("hello", [WEB_SEARCH_TOOL], system="be terse")
    config = models.calls[0]["config"]

    assert config.response_mime_type is None
    assert config.response_schema is None
    assert config.system_instruction == "be terse"


async def test_tool_calls_are_paced_and_retried_like_any_other_call() -> None:
    """A tool turn must not bypass the rate-limit gate to feel faster."""
    client, models = build(
        FakeAPIError(429), FakeFunctionCall("web_search", {"query": "q"})
    )

    call = await client.generate_with_tools("hello", [WEB_SEARCH_TOOL])

    assert call is not None
    assert len(models.calls) == 2


async def test_a_tool_call_and_a_structured_call_never_overlap() -> None:
    """The two phases of one turn share the semaphore-of-one."""
    client, models = build(
        FakeFunctionCall("web_search", {"query": "q"}), '{"value": "ok"}'
    )

    await client.generate_with_tools("phase one", [WEB_SEARCH_TOOL])
    await client.generate_structured("phase two", Sample)

    assert len(models.calls) == 2
    assert models.max_concurrent == 1


# --------------------------------------------------------------------------- #
# Schema reduction
#
# Gemini's response_schema is not JSON Schema. Handing it Pydantic's output
# verbatim 400s the whole request, which surfaced as every agent silently
# falling back to "pass" — a game that runs and does nothing.
# --------------------------------------------------------------------------- #


async def test_additional_properties_is_stripped() -> None:
    """`extra="forbid"` renders as additionalProperties, which Gemini rejects."""
    from app.models.agent_io import AgentDecision

    schema = to_gemini_schema(AgentDecision)

    def has_key(node: Any, key: str) -> bool:
        if isinstance(node, dict):
            return key in node or any(has_key(v, key) for v in node.values())
        if isinstance(node, list):
            return any(has_key(item, key) for item in node)
        return False

    assert not has_key(schema, "additionalProperties")
    assert not has_key(schema, "$ref")
    assert not has_key(schema, "$defs")


async def test_nested_models_are_inlined() -> None:
    """Nested models become $ref, and Gemini has no concept of references."""
    from app.models.agent_io import AgentDecision

    schema = to_gemini_schema(AgentDecision)
    offer = schema["properties"]["offer"]

    # The ProposedOffer body is present inline, not behind a reference.
    assert "properties" in offer
    assert set(offer["properties"]) == {"to", "resource", "amount"}


async def test_optional_fields_become_nullable() -> None:
    """Pydantic spells it anyOf[X, null]; Gemini spells it nullable."""
    from app.models.agent_io import AgentDecision

    schema = to_gemini_schema(AgentDecision)

    assert schema["properties"]["target_offer_id"]["nullable"] is True
    assert schema["properties"]["target_offer_id"]["type"] == "string"
    assert "anyOf" not in schema["properties"]["target_offer_id"]


async def test_required_fields_and_descriptions_survive() -> None:
    """The reduction must not strip what actually steers the model."""
    from app.models.agent_io import AgentDecision

    schema = to_gemini_schema(AgentDecision)

    assert "thought" in schema["required"]
    assert schema["properties"]["thought"]["description"]
    assert schema["properties"]["action"]["enum"] == ["offer", "accept", "reject", "pass"]


async def test_the_reduced_schema_is_what_reaches_the_provider() -> None:
    client, models = build('{"value": "ok"}')

    await client.generate_structured("hello", Sample)

    sent = models.calls[0]["config"].response_schema
    assert isinstance(sent, dict), "a Pydantic class would carry additionalProperties"
