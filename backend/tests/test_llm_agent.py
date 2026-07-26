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
from app.llm_client import LLMError
from app.models.schemas import OfferRecord, Pool


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


def make_context(
    agent_id: str = "cooperator",
    *,
    pending: list[PendingOffer] | None = None,
    recent: list[OfferRecord] | None = None,
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


def test_the_system_prompt_carries_only_this_agents_hidden_objective() -> None:
    agent, _ = make_agent()

    prompt = agent.system_prompt()

    assert persona_by_id("cooperator").objective.description in prompt
    for other in PERSONAS:
        if other.id != "cooperator":
            assert other.objective.description not in prompt
            assert other.private_directive not in prompt


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


def test_the_turn_prompt_never_mentions_another_agents_objective() -> None:
    agent, _ = make_agent()

    rendered = agent.render_turn(make_context())

    for persona in PERSONAS:
        assert persona.objective.description not in rendered


def test_build_llm_agents_returns_one_agent_per_persona_in_seating_order() -> None:
    agents = build_llm_agents(FakeLLM(), make_settings())  # type: ignore[arg-type]

    assert [agent.id for agent in agents] == [persona.id for persona in PERSONAS]
