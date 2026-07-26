"""The Tavily-backed `web_search` tool, in isolation.

No network: a fake stands in for the client at the `search()` seam, the same
way `test_llm_client.py` fakes the Gemini SDK.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.config import Settings
from app.search import (
    SNIPPET_CHARS,
    TOOL_NAME,
    WEB_SEARCH_DECLARATION,
    SearchError,
    SearchHit,
    WebSearchTool,
    render_hits,
)


def make_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "tavily_api_key": "fake",
        "tavily_max_results": 3,
        "tavily_timeout_seconds": 5.0,
        "_env_file": None,
    }
    base.update(overrides)
    return Settings(**base)


class FakeTavily:
    """Scripted stand-in. An outcome is a payload, an exception, or a float delay."""

    def __init__(self, *outcomes: Any) -> None:
        self._outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []

    async def search(self, query: str, **kwargs: Any) -> Any:
        self.calls.append({"query": query, **kwargs})
        await asyncio.sleep(0)
        if not self._outcomes:
            raise AssertionError("fake ran out of outcomes")
        item = self._outcomes.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, float):  # a slow call, for timeout tests
            await asyncio.sleep(item)
            return payload()
        return item


def payload(*results: dict[str, Any]) -> dict[str, Any]:
    if not results:
        results = (
            {"title": "Copper prices climb", "content": "Copper hit $4.20/lb.", "url": "https://a.example"},
        )
    return {"results": list(results)}


def build(*outcomes: Any, **settings_overrides: Any) -> tuple[WebSearchTool, FakeTavily]:
    fake = FakeTavily(*outcomes)
    return WebSearchTool(make_settings(**settings_overrides), client=fake), fake


# --------------------------------------------------------------------------- #
# Mapping
# --------------------------------------------------------------------------- #


async def test_a_tavily_result_becomes_a_hit() -> None:
    tool, fake = build(payload())

    hits = await tool.search("copper price")

    assert hits == [
        SearchHit(
            title="Copper prices climb",
            snippet="Copper hit $4.20/lb.",
            url="https://a.example",
        )
    ]
    assert fake.calls[0]["query"] == "copper price"
    assert fake.calls[0]["max_results"] == 3


async def test_the_query_is_stripped_before_being_sent() -> None:
    tool, fake = build(payload())

    await tool.search("  copper price  ")

    assert fake.calls[0]["query"] == "copper price"


async def test_an_empty_query_never_reaches_the_provider() -> None:
    tool, fake = build(payload())

    with pytest.raises(SearchError, match="empty search query"):
        await tool.search("   ")

    assert fake.calls == []


async def test_long_snippets_are_truncated() -> None:
    """Every hit is pasted into a follow-up prompt; length is a real cost."""
    tool, _ = build(
        payload({"title": "t", "content": "x" * 5_000, "url": "https://a.example"})
    )

    hits = await tool.search("q")

    assert len(hits[0].snippet) == SNIPPET_CHARS


async def test_results_are_capped_at_the_configured_maximum() -> None:
    many = [
        {"title": f"t{i}", "content": f"c{i}", "url": f"https://{i}.example"} for i in range(10)
    ]
    tool, _ = build(payload(*many), tavily_max_results=2)

    hits = await tool.search("q")

    assert len(hits) == 2


async def test_results_missing_a_url_or_snippet_are_dropped() -> None:
    tool, _ = build(
        payload(
            {"title": "no url", "content": "c", "url": ""},
            {"title": "no snippet", "content": "  ", "url": "https://b.example"},
            {"title": "good", "content": "c", "url": "https://c.example"},
        )
    )

    hits = await tool.search("q")

    assert [hit.url for hit in hits] == ["https://c.example"]


async def test_a_missing_title_falls_back_to_the_url() -> None:
    tool, _ = build(payload({"content": "c", "url": "https://a.example"}))

    hits = await tool.search("q")

    assert hits[0].title == "https://a.example"


# --------------------------------------------------------------------------- #
# Failure
# --------------------------------------------------------------------------- #


async def test_a_provider_error_becomes_a_search_error() -> None:
    tool, _ = build(RuntimeError("boom"))

    with pytest.raises(SearchError, match="search failed: boom"):
        await tool.search("q")


async def test_a_timeout_becomes_a_search_error() -> None:
    tool, _ = build(5.0, tavily_timeout_seconds=0.05)

    with pytest.raises(SearchError, match="timed out"):
        await tool.search("q")


async def test_a_malformed_payload_becomes_a_search_error() -> None:
    tool, _ = build({"unexpected": True})

    with pytest.raises(SearchError, match="no results array"):
        await tool.search("q")


async def test_zero_results_is_not_an_error() -> None:
    """A search that legitimately finds nothing must not break the turn."""
    tool, _ = build({"results": []})

    assert await tool.search("q") == []


# --------------------------------------------------------------------------- #
# Rendering and the tool declaration
# --------------------------------------------------------------------------- #


def test_hits_render_with_their_source_url() -> None:
    rendered = render_hits(
        [SearchHit(title="T", snippet="S", url="https://a.example")]
    )

    assert "T" in rendered and "S" in rendered and "https://a.example" in rendered


def test_no_hits_renders_a_readable_placeholder() -> None:
    assert "no usable results" in render_hits([])


def test_the_declaration_matches_the_documented_tool_contract() -> None:
    assert WEB_SEARCH_DECLARATION.name == TOOL_NAME == "web_search"
    assert list(WEB_SEARCH_DECLARATION.parameters.properties) == ["query"]
    assert WEB_SEARCH_DECLARATION.parameters.required == ["query"]
