"""Milestone 12: full-game dry runs.

A session is played from start to reveal and the whole result is checked for
internal consistency — conservation of the pool, an offer log that agrees with
the final holdings, and a well-formed reveal payload.

Run against mock agents so the outcome is deterministic and needs no API key.
Swapping in `LLMAgent` changes who decides what, not any invariant asserted
here, which is exactly why the engine was built against the `Agent` protocol.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.agents.mock_agent import RandomAgent, ScriptedAgent
from app.agents.personas import HUMAN_ID, PERSONAS
from app.config import Settings
from app.engine.negotiation import NegotiationEngine
from app.main import create_app
from app.models.agent_io import AgentDecision, ProposedOffer
from app.models.messages import WSMessage
from app.models.schemas import NegotiationState, OfferSchema

COOP, MAXI, TIT = "cooperator", "maximizer", "titfortat"


def make_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "anthropic_api_key": None,
        "use_mock_agents": True,
        "rounds": 6,
        "turn_delay_seconds": 0.0,
        "pool_resource": "budget",
        "pool_total": 100.0,
        "_env_file": None,
    }
    base.update(overrides)
    return Settings(**base)


class Recorder:
    def __init__(self) -> None:
        self.events: list[WSMessage] = []

    async def __call__(self, message: WSMessage) -> None:
        self.events.append(message)


async def play_full_game(seed_offset: int = 0, **overrides: Any) -> tuple[NegotiationEngine, Recorder]:
    recorder = Recorder()
    engine = NegotiationEngine(
        session_id="e2e",
        agents=[
            RandomAgent(persona.id, seed=index + seed_offset)
            for index, persona in enumerate(PERSONAS)
        ],
        settings=make_settings(**overrides),
        emit=recorder,
    )
    await engine.run()
    return engine, recorder


# --------------------------------------------------------------------------- #
# A complete game
# --------------------------------------------------------------------------- #


async def test_a_full_six_round_game_completes_and_reveals() -> None:
    engine, recorder = await play_full_game()

    assert engine.finished is True
    assert engine.round == 6
    assert engine.reveal is not None
    assert recorder.events[-1].type == "reveal"


async def test_the_reveal_payload_is_well_formed() -> None:
    engine, _ = await play_full_game()
    reveal = engine.reveal
    assert reveal is not None

    assert set(reveal.revealed_objectives) == {COOP, MAXI, TIT}
    assert set(reveal.scores) == {COOP, MAXI, TIT}
    assert set(reveal.holdings) == {COOP, MAXI, TIT, HUMAN_ID}
    assert all(0.0 <= score <= 1.0 for score in reveal.scores.values())
    assert all(text.strip() for text in reveal.revealed_objectives.values())
    assert reveal.final_state.revealed_objectives == reveal.revealed_objectives


async def test_the_reveal_serializes_to_json_the_frontend_can_consume() -> None:
    _, recorder = await play_full_game()

    frame = json.loads(recorder.events[-1].model_dump_json(by_alias=True))

    assert frame["type"] == "reveal"
    assert set(frame["payload"]) == {
        "revealed_objectives",
        "scores",
        "holdings",
        "final_state",
    }
    state = frame["payload"]["final_state"]
    assert set(state) == {
        "round",
        "pool",
        "agents",
        "trust_graph",
        "offer_log",
        "agent_thoughts",
        "revealed_objectives",
    }
    for entry in state["offer_log"]:
        assert "from" in entry and "from_" not in entry


# --------------------------------------------------------------------------- #
# Invariants that must hold however the game goes
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("seed_offset", [0, 10, 20, 30])
async def test_the_pool_is_conserved_regardless_of_how_play_unfolds(
    seed_offset: int,
) -> None:
    engine, _ = await play_full_game(seed_offset=seed_offset)

    assert sum(engine.holdings.values()) == pytest.approx(100.0, abs=1e-6)
    assert all(amount >= 0 for amount in engine.holdings.values())


@pytest.mark.parametrize("seed_offset", [0, 10, 20])
async def test_final_holdings_equal_the_even_split_plus_accepted_transfers(
    seed_offset: int,
) -> None:
    """The offer log really is an audit trail: it explains the final position."""
    engine, _ = await play_full_game(seed_offset=seed_offset)

    reconstructed = {party: 25.0 for party in engine.holdings}
    for entry in engine.offer_log:
        if entry.accepted:
            reconstructed[entry.from_] -= entry.amount
            reconstructed[entry.to] += entry.amount

    for party, amount in engine.holdings.items():
        assert amount == pytest.approx(reconstructed[party], abs=1e-6)


async def test_trust_weights_stay_in_range_all_game() -> None:
    engine, recorder = await play_full_game()

    for edge in engine.snapshot().trust_graph.edges:
        assert 0.0 <= edge.weight <= 1.0

    for event in recorder.events:
        if event.type == "graph_update":
            for edge in event.payload.edges:
                assert 0.0 <= edge.weight <= 1.0


async def test_every_offer_is_either_pending_or_resolved_exactly_once() -> None:
    engine, recorder = await play_full_game()

    resolved = [entry for entry in engine.offer_log if entry.accepted is not None]
    pending = [entry for entry in engine.offer_log if entry.accepted is None]

    assert len(resolved) + len(pending) == len(engine.offer_log)
    # Anything still unresolved must genuinely still be in the pending queue.
    assert len(pending) == len(engine.pending)


async def test_thoughts_are_produced_by_every_agent_every_round() -> None:
    engine, _ = await play_full_game()

    assert len(engine.thoughts) == 6 * len(PERSONAS)
    assert {thought.agent_id for thought in engine.thoughts} == {COOP, MAXI, TIT}


async def test_no_hidden_objective_appears_before_the_reveal_frame() -> None:
    _, recorder = await play_full_game()

    secrets = [persona.objective.description for persona in PERSONAS]
    for event in recorder.events[:-1]:  # everything except the reveal itself
        blob = event.model_dump_json(by_alias=True)
        for secret in secrets:
            assert secret not in blob


# --------------------------------------------------------------------------- #
# The human in the loop
# --------------------------------------------------------------------------- #


async def test_a_human_offer_can_be_injected_and_accepted_mid_game() -> None:
    """The full human path: inject, an agent sees it, accepts, resource moves."""
    recorder = Recorder()
    engine = NegotiationEngine(
        session_id="human-e2e",
        agents=[
            ScriptedAgent(COOP, []),
            ScriptedAgent(
                MAXI,
                [AgentDecision(action="accept", target_offer_id="o1", thought="I'll take it.")],
            ),
            ScriptedAgent(TIT, []),
        ],
        settings=make_settings(rounds=2),
        emit=recorder,
    )

    await engine.inject_offer(
        OfferSchema(from_=HUMAN_ID, to=MAXI, resource="budget", amount=15.0)
    )
    await engine.run()

    assert engine.holdings[HUMAN_ID] == 10.0
    assert engine.holdings[MAXI] == 40.0
    human_entries = [e for e in engine.offer_log if e.from_ == HUMAN_ID]
    assert len(human_entries) == 1 and human_entries[0].accepted is True
    # Trust in the human rose, because the human gave something away.
    assert engine.graph.weight(MAXI, HUMAN_ID) > 0.5


async def test_the_human_can_still_be_scored_out_of_the_pool() -> None:
    """The human holds resource and affects everyone's objectives."""
    engine, _ = await play_full_game()

    assert HUMAN_ID in engine.holdings
    assert HUMAN_ID not in (engine.reveal.scores if engine.reveal else {})


# --------------------------------------------------------------------------- #
# Through the running application
# --------------------------------------------------------------------------- #


def test_a_session_played_through_the_api_reaches_a_valid_reveal() -> None:
    """Start via HTTP, let it run, and read the reveal back off /state."""
    client = TestClient(create_app(make_settings(rounds=4)))

    assert client.post("/api/session/start").status_code == 200

    for _ in range(400):
        body = client.get("/api/session/state").json()
        if body["revealed_objectives"] is not None:
            break
    else:  # pragma: no cover
        pytest.fail("the session never reached the reveal")

    state = NegotiationState.model_validate(body)
    assert state.round == 4
    assert state.revealed_objectives is not None
    assert set(state.revealed_objectives) == {COOP, MAXI, TIT}
    assert len(state.agent_thoughts) == 4 * len(PERSONAS)
    assert len(state.trust_graph.edges) == 12

    # And the session can be torn down and started again cleanly.
    assert client.post("/api/session/reset").json()["status"] == "reset"
    assert client.get("/api/session/state").status_code == 404
    assert client.post("/api/session/start").status_code == 200
