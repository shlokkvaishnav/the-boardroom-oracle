"""Milestone 5: the LLM agent's contract with the model.

No network: a fake client returns canned bodies so the retry-then-fallback
path, the prompt contents, and the request shape are all assertable.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.agents.base import PendingOffer, TurnContext
from app.agents.llm_agent import DECISION_SCHEMA, LLMAgent, build_llm_agents
from app.agents.opponent_model import BeliefSet
from app.agents.personas import PERSONAS, all_agent_infos, persona_by_id
from app.config import Settings
from app.models.schemas import OfferRecord, Pool


# --------------------------------------------------------------------------- #
# Fake Anthropic client
# --------------------------------------------------------------------------- #


class FakeBlock:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class FakeResponse:
    def __init__(self, text: str, stop_reason: str = "end_turn") -> None:
        self.content = [FakeBlock(text)]
        self.stop_reason = stop_reason


class FakeMessages:
    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> FakeResponse:
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("fake client ran out of scripted responses")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeClient:
    def __init__(self, *responses: Any) -> None:
        self.messages = FakeMessages(list(responses))


def make_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "anthropic_api_key": "test",
        "anthropic_model": "claude-opus-5",
        "anthropic_effort": "low",
        "anthropic_max_tokens": 1024,
        "pool_resource": "budget",
        "pool_total": 100.0,
        "_env_file": None,
    }
    base.update(overrides)
    return Settings(**base)


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


def make_agent(*responses: Any, **settings_overrides: Any) -> tuple[LLMAgent, FakeClient]:
    client = FakeClient(*responses)
    agent = LLMAgent(persona_by_id("cooperator"), client, make_settings(**settings_overrides))  # type: ignore[arg-type]
    return agent, client


VALID = json.dumps(
    {
        "action": "offer",
        "offer": {"to": "maximizer", "resource": "budget", "amount": 10},
        "target_offer_id": None,
        "thought": "Opening generously.",
        "opponent_updates": [{"agent_id": "maximizer", "trust_delta": 0.1, "note": "civil"}],
    }
)


# --------------------------------------------------------------------------- #
# Happy path
# --------------------------------------------------------------------------- #


async def test_a_valid_response_is_returned_after_one_call() -> None:
    agent, client = make_agent(FakeResponse(VALID))

    decision = await agent.decide(make_context())

    assert decision.action == "offer"
    assert decision.offer is not None and decision.offer.amount == 10
    assert decision.opponent_updates[0].agent_id == "maximizer"
    assert len(client.messages.calls) == 1


async def test_the_request_carries_the_schema_and_the_effort_setting() -> None:
    agent, client = make_agent(FakeResponse(VALID), anthropic_effort="xhigh")

    await agent.decide(make_context())
    call = client.messages.calls[0]

    assert call["model"] == "claude-opus-5"
    assert call["output_config"]["format"] == {
        "type": "json_schema",
        "schema": DECISION_SCHEMA,
    }
    assert call["output_config"]["effort"] == "xhigh"
    assert call["max_tokens"] == 1024


def test_the_decision_schema_is_strict_enough_for_structured_outputs() -> None:
    """Structured outputs require additionalProperties: false on every object."""

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                assert node.get("additionalProperties") is False, node
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(DECISION_SCHEMA)


# --------------------------------------------------------------------------- #
# Retry, then fall back
# --------------------------------------------------------------------------- #


async def test_malformed_json_is_retried_once_and_can_succeed() -> None:
    agent, client = make_agent(FakeResponse("not json at all"), FakeResponse(VALID))

    decision = await agent.decide(make_context())

    assert decision.action == "offer"
    assert len(client.messages.calls) == 2


async def test_schema_violations_are_retried_too() -> None:
    """`offer` action with no offer object — caught by the model validator."""
    invalid = json.dumps({"action": "offer", "thought": "I'll propose something"})
    agent, client = make_agent(FakeResponse(invalid), FakeResponse(VALID))

    decision = await agent.decide(make_context())

    assert decision.action == "offer"
    assert len(client.messages.calls) == 2


async def test_two_failures_fall_back_to_a_safe_pass() -> None:
    agent, client = make_agent(FakeResponse("garbage"), FakeResponse("still garbage"))

    decision = await agent.decide(make_context())

    assert decision.action == "pass"
    assert decision.offer is None and decision.target_offer_id is None
    assert len(client.messages.calls) == 2


async def test_the_retry_feeds_the_error_back_and_ends_on_a_user_turn() -> None:
    """A trailing assistant message would be a prefill, which the API rejects."""
    agent, client = make_agent(FakeResponse("garbage"), FakeResponse(VALID))

    await agent.decide(make_context())
    retry_messages = client.messages.calls[1]["messages"]

    assert [message["role"] for message in retry_messages] == ["user", "assistant", "user"]
    assert retry_messages[1]["content"] == "garbage"
    assert "rejected" in retry_messages[2]["content"]


async def test_a_transport_failure_is_retried_then_falls_back() -> None:
    agent, client = make_agent(
        RuntimeError("connection reset"), RuntimeError("connection reset again")
    )

    decision = await agent.decide(make_context())

    assert decision.action == "pass"
    assert "connection reset" in decision.thought
    assert len(client.messages.calls) == 2


async def test_a_transport_failure_followed_by_success_recovers() -> None:
    agent, client = make_agent(RuntimeError("blip"), FakeResponse(VALID))

    decision = await agent.decide(make_context())

    assert decision.action == "offer"


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
async def test_unusable_stop_reasons_are_treated_as_failures(stop_reason: str) -> None:
    agent, _ = make_agent(
        FakeResponse(VALID, stop_reason=stop_reason), FakeResponse(VALID)
    )

    decision = await agent.decide(make_context())

    # Second attempt succeeded, proving the first was discarded.
    assert decision.action == "offer"


async def test_decide_never_raises_into_the_game_loop() -> None:
    agent, _ = make_agent(RuntimeError("boom"), RuntimeError("boom"))

    decision = await agent.decide(make_context())

    assert decision.action == "pass"


# --------------------------------------------------------------------------- #
# Sanitising near-misses
# --------------------------------------------------------------------------- #


async def test_an_invented_resource_name_is_coerced_to_the_table_resource() -> None:
    body = json.dumps(
        {
            "action": "offer",
            "offer": {"to": "maximizer", "resource": "budget units", "amount": 8},
            "thought": "Small opener.",
        }
    )
    agent, client = make_agent(FakeResponse(body))

    decision = await agent.decide(make_context())

    assert decision.offer is not None and decision.offer.resource == "budget"
    assert len(client.messages.calls) == 1  # corrected, not retried


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
    body = json.dumps({"action": "accept", "target_offer_id": "o1", "thought": "Deal."})
    agent, _ = make_agent(FakeResponse(body))

    decision = await agent.decide(make_context(pending=pending))

    assert decision.target_offer_id == "o7"


async def test_a_wrong_offer_id_becomes_a_pass_when_the_target_is_ambiguous() -> None:
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
    body = json.dumps({"action": "accept", "target_offer_id": "o99", "thought": "Deal."})
    agent, _ = make_agent(FakeResponse(body))

    decision = await agent.decide(make_context(pending=pending))

    assert decision.action == "pass"
    assert decision.thought == "Deal."  # the model's own words are preserved


async def test_answering_with_nothing_pending_becomes_a_pass() -> None:
    body = json.dumps({"action": "accept", "target_offer_id": "o1", "thought": "Yes."})
    agent, _ = make_agent(FakeResponse(body))

    decision = await agent.decide(make_context(pending=[]))

    assert decision.action == "pass"


# --------------------------------------------------------------------------- #
# Prompt contents
# --------------------------------------------------------------------------- #


def test_the_system_prompt_carries_only_this_agents_hidden_objective() -> None:
    agent = LLMAgent(persona_by_id("cooperator"), FakeClient(), make_settings())  # type: ignore[arg-type]

    prompt = agent.system_prompt()

    assert persona_by_id("cooperator").objective.description in prompt
    for other in PERSONAS:
        if other.id != "cooperator":
            assert other.objective.description not in prompt
            assert other.private_directive not in prompt


def test_the_system_prompt_is_stable_across_turns() -> None:
    """Byte-stable prefix — cheap to cache and easy to reason about."""
    agent = LLMAgent(persona_by_id("maximizer"), FakeClient(), make_settings())  # type: ignore[arg-type]

    assert agent.system_prompt() == agent.system_prompt()


def test_the_turn_prompt_shows_pending_offers_with_their_ids() -> None:
    agent = LLMAgent(persona_by_id("cooperator"), FakeClient(), make_settings())  # type: ignore[arg-type]
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
    agent = LLMAgent(persona_by_id("cooperator"), FakeClient(), make_settings())  # type: ignore[arg-type]

    rendered = agent.render_turn(make_context())

    assert "cannot accept or reject anything" in rendered


def test_the_turn_prompt_includes_holdings_round_and_beliefs() -> None:
    agent = LLMAgent(persona_by_id("cooperator"), FakeClient(), make_settings())  # type: ignore[arg-type]

    rendered = agent.render_turn(make_context())

    assert "ROUND 2 OF 6" in rendered
    assert "cooperator: 25 budget (25% of the pool)" in rendered
    assert "my_trust" in rendered and "public_trust" in rendered


def test_the_turn_prompt_never_mentions_another_agents_objective() -> None:
    agent = LLMAgent(persona_by_id("cooperator"), FakeClient(), make_settings())  # type: ignore[arg-type]

    rendered = agent.render_turn(make_context())

    for persona in PERSONAS:
        assert persona.objective.description not in rendered


def test_build_llm_agents_returns_one_agent_per_persona_in_seating_order() -> None:
    agents = build_llm_agents(FakeClient(), make_settings())  # type: ignore[arg-type]

    assert [agent.id for agent in agents] == [persona.id for persona in PERSONAS]
