"""Live web search, offered to agents as a Gemini function-calling tool.

This module owns the provider contact for search the way `llm_client.py` owns it
for the LLM: one class, one injectable client, one error type. Nothing else in
the codebase imports `tavily`.

Two deliberate constraints, both about keeping a turn affordable:

**Results are few and short.** Every hit is pasted back into a follow-up prompt
that already carries the full negotiation state, so `tavily_max_results` defaults
to 3 and each snippet is truncated. A verbose tool result crowds out the thing
the agent is actually supposed to reason about.

**Failure is never fatal.** A search that errors or times out returns nothing and
lets the turn continue without it. The agent loses a fact, not its turn — the
same principle as the safe-default decision in `agents/llm_agent.py`.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from google.genai import types
from pydantic import BaseModel

from app.config import Settings

logger = logging.getLogger("boardroom.search")

__all__ = [
    "SearchError",
    "SearchHit",
    "WebSearchTool",
    "WEB_SEARCH_DECLARATION",
    "WEB_SEARCH_TOOL",
    "TOOL_NAME",
]

TOOL_NAME = "web_search"

#: Snippets are truncated to this many characters before going back to the model.
SNIPPET_CHARS = 320


class SearchError(RuntimeError):
    """Any failure to get usable results out of the search provider."""


class SearchHit(BaseModel):
    """One result, in the shape the tool contract promises the model."""

    title: str
    snippet: str
    url: str

    def render(self) -> str:
        """One hit as plain text for the follow-up prompt."""
        return f"- {self.title}\n  {self.snippet}\n  source: {self.url}"


def render_hits(hits: list[SearchHit]) -> str:
    """Tool results as a text block to append to the follow-up prompt."""
    if not hits:
        return "(the search returned no usable results)"
    return "\n".join(hit.render() for hit in hits)


# --------------------------------------------------------------------------- #
# The tool as Gemini sees it
# --------------------------------------------------------------------------- #

WEB_SEARCH_DECLARATION = types.FunctionDeclaration(
    name=TOOL_NAME,
    description=(
        "Search the live web for current, factual information. Use this only when "
        "the negotiation's context topic requires a fact you do not already know — "
        "a current price, a recent event, a real-world figure. Do not use it for "
        "general reasoning, and do not use it more than once per turn."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "query": types.Schema(
                type=types.Type.STRING,
                description="A short, specific search query — a few keywords, not a sentence.",
            ),
        },
        required=["query"],
    ),
)

WEB_SEARCH_TOOL = types.Tool(function_declarations=[WEB_SEARCH_DECLARATION])


# --------------------------------------------------------------------------- #
# The tool as the server runs it
# --------------------------------------------------------------------------- #


class WebSearchTool:
    """Tavily-backed `web_search`, with its own timeout and no retries.

    No retry loop on purpose: the caller is mid-turn behind the LLM client's
    rate-limit gate, and a second search would push the turn further past the
    demo's timing budget than a missing fact costs.
    """

    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        self._settings = settings
        self._client = client if client is not None else self._build_client()

    def _build_client(self) -> Any:
        # Imported here rather than at module scope so tests can inject a fake
        # without the package being installed, matching `speech/transcribe.py`.
        from tavily import AsyncTavilyClient

        return AsyncTavilyClient(api_key=self._settings.tavily_api_key)

    async def search(self, query: str) -> list[SearchHit]:
        """Run one search. Raises `SearchError`; never returns partial garbage."""
        cleaned = query.strip()
        if not cleaned:
            raise SearchError("empty search query")

        try:
            raw = await asyncio.wait_for(
                self._client.search(
                    cleaned,
                    max_results=self._settings.tavily_max_results,
                ),
                timeout=self._settings.tavily_timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            raise SearchError(
                f"search timed out after {self._settings.tavily_timeout_seconds}s"
            ) from exc
        except SearchError:
            raise
        except Exception as exc:  # provider SDK raises its own zoo of errors
            raise SearchError(f"search failed: {exc}") from exc

        hits = self._to_hits(raw)
        logger.info("web_search %r -> %d hit(s)", cleaned, len(hits))
        return hits

    def _to_hits(self, raw: Any) -> list[SearchHit]:
        """Map a Tavily payload to hits, dropping anything unusable."""
        results = (raw or {}).get("results") if isinstance(raw, dict) else None
        if not isinstance(results, list):
            raise SearchError("search returned no results array")

        hits: list[SearchHit] = []
        for item in results[: self._settings.tavily_max_results]:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            # Tavily calls the snippet `content`.
            snippet = str(item.get("content") or "").strip()
            if not url or not snippet:
                continue
            hits.append(
                SearchHit(
                    title=str(item.get("title") or url).strip(),
                    snippet=snippet[:SNIPPET_CHARS],
                    url=url,
                )
            )

        return hits
