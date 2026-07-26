"""The agent's contract with the model.

No network: a fake `LLMClient` returns canned dicts so the
validate-retry-fallback ladder and the prompt contents are assertable. Pacing
and transport retries are the client's job and are tested in
`test_llm_client.py`.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.base import PendingOffer, TurnContext
from app.agents.llm_agent import LLMAgent, build_llm_agents
from app.agents.opponent_model import BeliefSet
from app.agents.personas import PERSONAS, all_agent_infos, persona_by_id
from app.config import Settings
from app.llm_client import LLMError, ToolCallRequest
from app.models.schemas import OfferRecord, Pool
from app.search import SearchError, SearchHit


def make_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "gemini_api_key": "fake",
        "gemini_model": "gemini-3.6-flash",
        "pool_resource": "budget",
        "pool_total": 100.0,
        "_env_file": None,
    }
    base.update(overrides)
    return Settings(**base)


class FakeLLM:
    """Stands in for `LLMClient`, returning pre-scripted parsed payloads."""

    def __init__(self, *outcomes: Any) -> None:
        self._outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []
        #: Phase-one calls. Empty means the tool was never offered.
        self.tool_calls: list[dict[str, Any]] = []
        #: What phase one returns: a ToolCallRequest, None, or an Exception.
        self.tool_call: Any = None

    async def generate_structured(
        self, prompt: str, schema: type, *, system: str | None = None
    ) -> dict[str, Any]:
        self.calls.append({"prompt": prompt, "schema": schema, "system": system})
        if not self._outcomes:
            raise AssertionError("fake LLM ran out of outcomes")
        item = self._outcomes.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def generate_with_tools(
        self, prompt: str, tools: list[Any], *, system: str | None = None
    ) -> Any:
        self.tool_calls.append({"prompt": prompt, "tools": tools, "system": system})
        if isinstance(self.tool_call, Exception):
            raise self.tool_call
        return self.tool_call


def make_context(
    agent_id: str = "cooperator",
    *,
    pending: list[PendingOffer] | None = None,
    recent: list[OfferRecord] | None = None,
    context_topic: str | None = None,
) -> TurnContext:
    parties = all_agent_infos()
    party_ids = [party.id for party in parties]
    return TurnContext(
        agent_id=agent_id,
        round=2,
        total_rounds=6,
        pool=Pool(resource="budget", total=100.0),
        holdings={pid: 25.0 for pid in party_ids},
        parties=parties,
        pending_offers=pending or [],
        recent_offers=recent or [],
        beliefs=BeliefSet.for_agent(agent_id, party_ids),
        trust_row={pid: 0.5 for pid in party_ids if pid != agent_id},
        context_topic=context_topic,
    )


def make_agent(*outcomes: Any, persona: str = "cooperator") -> tuple[LLMAgent, FakeLLM]:
    llm = FakeLLM(*outcomes)
    agent = LLMAgent(persona_by_id(persona), llm, make_settings())  # type: ignore[arg-type]
    return agent, llm


VALID = {
    "action": "offer",
    "offer": {"to": "maximizer", "resource": "budget", "amount": 10},
    "target_offer_id": None,
    "thought": "Opening generously.",
    "opponent_updates": [{"agent_id": "maximizer", "trust_delta": 0.1, "note": "civil"}],
}


# --------------------------------------------------------------------------- #
# Happy path
# --------------------------------------------------------------------------- #


async def test_a_valid_response_is_returned_after_one_call() -> None:
    agent, llm = make_agent(VALID)

    decision = await agent.decide(make_context())

    assert decision.action == "offer"
    assert decision.offer is not None and decision.offer.amount == 10
    assert decision.opponent_updates[0].agent_id == "maximizer"
    assert len(llm.calls) == 1


async def test_the_schema_and_system_prompt_are_passed_to_the_client() -> None:
    from app.models.agent_io import AgentDecision

    agent, llm = make_agent(VALID)

    await agent.decide(make_context())

    assert llm.calls[0]["schema"] is AgentDecision
    assert "Ada" in (llm.calls[0]["system"] or "")


# --------------------------------------------------------------------------- #
# Retry, then fall back
# --------------------------------------------------------------------------- #


async def test_a_schema_violation_is_retried_once_and_can_succeed() -> None:
    """`offer` action with no offer object — caught by the model validator."""
    agent, llm = make_agent({"action": "offer", "thought": "I'll propose something"}, VALID)

    decision = await agent.decide(make_context())

    assert decision.action == "offer"
    assert len(llm.calls) == 2


async def test_two_invalid_responses_fall_back_to_a_safe_pass() -> None:
    agent, llm = make_agent(
        {"action": "accept", "thought": "yes"},  # missing target_offer_id
        {"action": "accept", "thought": "yes again"},
    )

    decision = await agent.decide(make_context())

    assert decision.action == "pass"
    assert decision.offer is None and decision.target_offer_id is None
    assert len(llm.calls) == 2


async def test_the_retry_feeds_the_validation_error_back_into_the_prompt() -> None:
    agent, llm = make_agent({"action": "offer", "thought": "oops"}, VALID)

    await agent.decide(make_context())

    assert "rejected" in llm.calls[1]["prompt"]
    assert "target_offer_id" in llm.calls[1]["prompt"]


async def test_a_provider_failure_falls_back_without_crashing() -> None:
    agent, llm = make_agent(LLMError("429 after 3 attempts"), LLMError("still down"))

    decision = await agent.decide(make_context())

    assert decision.action == "pass"
    # The fallback carries the most recent failure, not the first.
    assert "still down" in decision.thought
    assert len(llm.calls) == 2


async def test_a_provider_failure_followed_by_success_recovers() -> None:
    agent, _ = make_agent(LLMError("transient"), VALID)

    decision = await agent.decide(make_context())

    assert decision.action == "offer"


async def test_decide_never_raises_into_the_game_loop() -> None:
    agent, _ = make_agent(LLMError("boom"), LLMError("boom"))

    decision = await agent.decide(make_context())

    assert decision.action == "pass"


# --------------------------------------------------------------------------- #
# Sanitising near-misses
# --------------------------------------------------------------------------- #


async def test_an_invented_resource_name_is_coerced_to_the_table_resource() -> None:
    agent, llm = make_agent(
        {
            "action": "offer",
            "offer": {"to": "maximizer", "resource": "budget units", "amount": 8},
            "thought": "Small opener.",
        }
    )

    decision = await agent.decide(make_context())

    assert decision.offer is not None and decision.offer.resource == "budget"
    assert len(llm.calls) == 1  # corrected, not retried — retries cost quota


async def test_a_wrong_offer_id_is_retargeted_when_only_one_is_pending() -> None:
    pending = [
        PendingOffer(
            id="o7",
            round=1,
            from_id="maximizer",
            to_id="cooperator",
            resource="budget",
            amount=5.0,
            log_index=0,
        )
    ]
    agent, _ = make_agent({"action": "accept", "target_offer_id": "o1", "thought": "Deal."})

    decision = await agent.decide(make_context(pending=pending))

    assert decision.target_offer_id == "o7"


async def test_a_wrong_offer_id_becomes_a_pass_when_ambiguous() -> None:
    pending = [
        PendingOffer(
            id=f"o{index}",
            round=1,
            from_id="maximizer",
            to_id="cooperator",
            resource="budget",
            amount=5.0,
            log_index=index,
        )
        for index in (1, 2)
    ]
    agent, _ = make_agent({"action": "accept", "target_offer_id": "o99", "thought": "Deal."})

    decision = await agent.decide(make_context(pending=pending))

    assert decision.action == "pass"
    assert decision.thought == "Deal."  # the model's own words are preserved


async def test_answering_with_nothing_pending_becomes_a_pass() -> None:
    agent, _ = make_agent({"action": "accept", "target_offer_id": "o1", "thought": "Yes."})

    decision = await agent.decide(make_context(pending=[]))

    assert decision.action == "pass"


# --------------------------------------------------------------------------- #
# Prompt contents
# --------------------------------------------------------------------------- #


def test_the_system_prompt_is_stable_across_turns() -> None:
    agent, _ = make_agent(persona="maximizer")

    assert agent.system_prompt() == agent.system_prompt()


def test_the_turn_prompt_shows_pending_offers_with_their_ids() -> None:
    agent, _ = make_agent()
    pending = [
        PendingOffer(
            id="o4",
            round=1,
            from_id="maximizer",
            to_id="cooperator",
            resource="budget",
            amount=7.0,
            log_index=0,
        )
    ]

    rendered = agent.render_turn(make_context(pending=pending))

    assert "[o4]" in rendered
    assert "maximizer offers you 7 budget" in rendered


def test_the_turn_prompt_states_when_nothing_is_pending() -> None:
    agent, _ = make_agent()

    assert "cannot accept or reject anything" in agent.render_turn(make_context())


def test_the_turn_prompt_includes_holdings_round_and_beliefs() -> None:
    agent, _ = make_agent()

    rendered = agent.render_turn(make_context())

    assert "ROUND 2 OF 6" in rendered
    assert "cooperator: 25 budget (25% of the pool)" in rendered
    assert "my_trust" in rendered and "public_trust" in rendered


def test_build_llm_agents_returns_one_agent_per_persona_in_seating_order() -> None:
    agents = build_llm_agents(FakeLLM(), make_settings())  # type: ignore[arg-type]

    assert [agent.id for agent in agents] == [persona.id for persona in PERSONAS]


# --------------------------------------------------------------------------- #
# Session context topic
#
# A shared real-world premise, added to the same game rather than changing it.
# --------------------------------------------------------------------------- #


def test_the_context_topic_appears_in_the_system_prompt() -> None:
    agent, _ = make_agent()

    prompt = agent.system_prompt("the 2026 copper supply squeeze")

    assert "the 2026 copper supply squeeze" in prompt
    # The topic must be argued about, not narrated around: these are the
    # instructions that turn move-captions into an actual exchange.
    assert "Take a" in prompt and "position" in prompt
    assert "not what" in prompt  # "...what the argument is *over*, not *about*"


def test_no_context_topic_leaves_the_system_prompt_as_it_was() -> None:
    """A plain session must be byte-identical to before the feature existed."""
    agent, _ = make_agent()

    assert agent.system_prompt(None) == agent.system_prompt()
    assert "THE MATTER ON THE TABLE" not in agent.system_prompt()


def test_the_context_topic_is_stable_across_turns() -> None:
    """Session-level, not per-turn — it must not make the system prompt volatile."""
    agent, _ = make_agent()
    topic = "the 2026 copper supply squeeze"

    assert agent.system_prompt(topic) == agent.system_prompt(topic)


async def test_the_turn_context_topic_reaches_the_system_prompt() -> None:
    agent, llm = make_agent(
        {"action": "pass", "thought": "waiting", "opponent_updates": []}
    )

    await agent.decide(make_context(context_topic="lithium prices"))

    assert "lithium prices" in llm.calls[0]["system"]


# --------------------------------------------------------------------------- #
# Web search turns
#
# Phase one decides whether to look something up; phase two is the ordinary
# structured call. The no-topic path must stay exactly one call.
# --------------------------------------------------------------------------- #


class FakeSearch:
    """Stands in for `WebSearchTool`."""

    def __init__(self, *outcomes: Any) -> None:
        self._outcomes = list(outcomes) or [[]]
        self.queries: list[str] = []

    async def search(self, query: str) -> Any:
        self.queries.append(query)
        item = self._outcomes.pop(0) if self._outcomes else []
        if isinstance(item, Exception):
            raise item
        return item


def hit(snippet: str = "Copper hit $4.20/lb.", url: str = "https://a.example") -> SearchHit:
    return SearchHit(title="t", snippet=snippet, url=url)


def make_searching_agent(
    *outcomes: Any,
    tool_call: Any = None,
    search_outcomes: tuple[Any, ...] = (),
) -> tuple[LLMAgent, FakeLLM, FakeSearch]:
    llm = FakeLLM(*outcomes)
    llm.tool_call = tool_call  # type: ignore[attr-defined]
    search = FakeSearch(*search_outcomes)
    agent = LLMAgent(
        persona_by_id("cooperator"), llm, make_settings(), search=search  # type: ignore[arg-type]
    )
    return agent, llm, search


async def test_without_a_context_topic_nothing_is_searched_and_one_call_is_made() -> None:
    """The regression guard: a plain session must be untouched by this feature."""
    agent, llm, search = make_searching_agent(VALID)

    decision = await agent.decide(make_context())

    assert decision.searched == []
    assert decision.llm_calls == 1
    assert search.queries == []
    assert llm.tool_calls == []


async def test_a_topic_offers_the_tool_and_a_declined_search_costs_two_calls() -> None:
    agent, llm, search = make_searching_agent(VALID, tool_call=None)

    decision = await agent.decide(make_context(context_topic="copper"))

    assert llm.tool_calls, "the tool was never offered"
    assert decision.searched == []
    assert decision.llm_calls == 2
    assert search.queries == []


async def test_a_requested_search_runs_and_is_recorded() -> None:
    agent, llm, search = make_searching_agent(
        VALID,
        tool_call=ToolCallRequest(name="web_search", args={"query": "copper price"}),
        search_outcomes=([hit()],),
    )

    decision = await agent.decide(make_context(context_topic="copper"))

    assert search.queries == ["copper price"]
    assert len(decision.searched) == 1
    assert decision.searched[0].query == "copper price"
    assert decision.searched[0].source_url == "https://a.example"
    assert decision.llm_calls == 2


async def test_search_results_are_fed_into_the_structured_call() -> None:
    """The whole point: phase two must actually see what phase one found."""
    agent, llm, _ = make_searching_agent(
        VALID,
        tool_call=ToolCallRequest(name="web_search", args={"query": "copper price"}),
        search_outcomes=([hit(snippet="Copper hit $4.20/lb.")],),
    )

    await agent.decide(make_context(context_topic="copper"))

    assert "Copper hit $4.20/lb." in llm.calls[0]["prompt"]
    assert "https://a.example" in llm.calls[0]["prompt"]


async def test_only_one_search_happens_per_turn() -> None:
    """The cap is structural: phase one runs once, whatever the model wants."""
    agent, _, search = make_searching_agent(
        VALID,
        tool_call=ToolCallRequest(name="web_search", args={"query": "one"}),
        search_outcomes=([hit()], [hit()]),
    )

    decision = await agent.decide(make_context(context_topic="copper"))

    assert len(search.queries) == 1
    assert decision.llm_calls == 2


async def test_a_failed_search_degrades_to_an_ordinary_turn() -> None:
    agent, _, _ = make_searching_agent(
        VALID,
        tool_call=ToolCallRequest(name="web_search", args={"query": "copper"}),
        search_outcomes=(SearchError("provider down"),),
    )

    decision = await agent.decide(make_context(context_topic="copper"))

    assert decision.action == "offer"  # the turn still happened
    assert decision.searched == []


async def test_a_failed_search_probe_degrades_to_an_ordinary_turn() -> None:
    agent, _, search = make_searching_agent(VALID, tool_call=LLMError("429 forever"))

    decision = await agent.decide(make_context(context_topic="copper"))

    assert decision.action == "offer"
    assert decision.searched == []
    assert search.queries == []


async def test_an_empty_query_is_not_searched() -> None:
    agent, _, search = make_searching_agent(
        VALID, tool_call=ToolCallRequest(name="web_search", args={"query": "  "})
    )

    await agent.decide(make_context(context_topic="copper"))

    assert search.queries == []


async def test_an_unknown_tool_name_is_ignored() -> None:
    agent, _, search = make_searching_agent(
        VALID, tool_call=ToolCallRequest(name="rm_rf", args={"query": "x"})
    )

    await agent.decide(make_context(context_topic="copper"))

    assert search.queries == []


async def test_search_results_survive_the_validation_retry() -> None:
    """A retry must not reason from less than the first attempt did."""
    agent, llm, _ = make_searching_agent(
        {"action": "offer", "thought": "bad", "opponent_updates": []},  # offer missing
        VALID,
        tool_call=ToolCallRequest(name="web_search", args={"query": "copper price"}),
        search_outcomes=([hit(snippet="Copper hit $4.20/lb.")],),
    )

    decision = await agent.decide(make_context(context_topic="copper"))

    assert "Copper hit $4.20/lb." in llm.calls[1]["prompt"]
    assert len(decision.searched) == 1
    assert decision.llm_calls == 3  # probe + two structured attempts


async def test_a_turn_with_no_search_tool_configured_never_offers_it() -> None:
    """No TAVILY_API_KEY: a topic session still runs, just without lookups."""
    llm = FakeLLM(VALID)
    agent = LLMAgent(persona_by_id("cooperator"), llm, make_settings())  # type: ignore[arg-type]

    decision = await agent.decide(make_context(context_topic="copper"))

    assert decision.llm_calls == 1
    assert decision.searched == []


def test_more_claims_than_asked_for_are_trimmed_not_retried() -> None:
    """A turn that over-claims is repaired in place.

    Rejecting it would cost a whole extra provider call to fix something a
    slice fixes for free — and the same over-long responses were already
    overflowing the token cap and costing retries of their own.
    """
    from app.agents.llm_agent import MAX_CLAIMS
    from app.models.agent_io import AgentDecision, Claim

    agent, _ = make_agent()
    context = make_context()
    decision = AgentDecision(
        action="pass",
        thought="Several points at once.",
        claims=[Claim(text=f"Point {i}.", kind="value") for i in range(5)],
    )

    cleaned = agent._sanitize(decision, context)

    assert len(cleaned.claims) == MAX_CLAIMS
    assert [c.text for c in cleaned.claims] == ["Point 0.", "Point 1."]
    assert cleaned.action == "pass", "trimming must not disturb the move itself"
