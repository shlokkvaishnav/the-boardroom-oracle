"""Milestone 4: the state machine, driven by mock agents.

Everything here is deterministic — scripted decisions, no LLM, no sleeping —
so turn order, the offer log, holdings arithmetic and the reveal can all be
asserted exactly.
"""

from __future__ import annotations

import pytest

from app.agents.mock_agent import RandomAgent, ScriptedAgent
from app.agents.personas import HUMAN_ID
from app.config import Settings
from app.engine.negotiation import NegotiationEngine, OfferRejected
from app.models.agent_io import AgentDecision, OpponentDelta, ProposedOffer
from app.models.messages import (
    GraphUpdateMessage,
    OfferMessage,
    ClosingMessage,
    RoundChangeMessage,
    ThoughtMessage,
    WSMessage,
)
from app.models.schemas import OfferSchema

COOP, MAXI, TIT = "cooperator", "maximizer", "titfortat"


def make_settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "rounds": 2,
        "turn_delay_seconds": 0.0,
        "pool_resource": "budget",
        "pool_total": 100.0,
        "_env_file": None,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


class Recorder:
    """Collects every emitted frame so tests can assert the stream."""

    def __init__(self) -> None:
        self.events: list[WSMessage] = []

    async def __call__(self, message: WSMessage) -> None:
        self.events.append(message)

    def of_type(self, kind: type) -> list[WSMessage]:
        return [event for event in self.events if isinstance(event, kind)]

    @property
    def types(self) -> list[str]:
        return [event.type for event in self.events]


def offer(to: str, amount: float, thought: str = "here you go") -> AgentDecision:
    return AgentDecision(
        action="offer",
        offer=ProposedOffer(to=to, resource="budget", amount=amount),
        thought=thought,
    )


def build_engine(
    scripts: dict[str, list[AgentDecision]] | None = None,
    *,
    recorder: Recorder | None = None,
    scribe: object | None = None,
    rapporteur: object | None = None,
    **settings_overrides: object,
) -> tuple[NegotiationEngine, Recorder, dict[str, ScriptedAgent]]:
    scripts = scripts or {}
    agents = {
        agent_id: ScriptedAgent(agent_id, scripts.get(agent_id, []))
        for agent_id in (COOP, MAXI, TIT)
    }
    recorder = recorder or Recorder()
    engine = NegotiationEngine(
        session_id="test-session",
        agents=list(agents.values()),
        settings=make_settings(**settings_overrides),
        emit=recorder,
        scribe=scribe,  # type: ignore[arg-type]
        rapporteur=rapporteur,  # type: ignore[arg-type]
    )
    return engine, recorder, agents


# --------------------------------------------------------------------------- #
# Setup
# --------------------------------------------------------------------------- #


def test_pool_starts_split_evenly_across_all_four_seats() -> None:
    engine, _, _ = build_engine()

    assert engine.holdings == {COOP: 25.0, MAXI: 25.0, TIT: 25.0, HUMAN_ID: 25.0}
    assert sum(engine.holdings.values()) == engine.pool.total


def test_initial_snapshot_is_a_clean_pre_game_state() -> None:
    engine, _, _ = build_engine()
    state = engine.snapshot()

    assert state.round == 0
    assert state.offer_log == []
    assert state.agent_thoughts == []
    assert state.closing_positions is None
    assert [agent.id for agent in state.agents] == [COOP, MAXI, TIT, HUMAN_ID]


def test_the_human_seat_is_flagged_and_the_ai_seats_are_not() -> None:
    engine, _, _ = build_engine()
    by_id = {agent.id: agent for agent in engine.snapshot().agents}

    assert by_id[HUMAN_ID].is_human is True
    assert all(not by_id[agent_id].is_human for agent_id in (COOP, MAXI, TIT))


# --------------------------------------------------------------------------- #
# Turn order and rounds
# --------------------------------------------------------------------------- #


async def test_agents_act_in_seating_order_once_per_round() -> None:
    engine, _, agents = build_engine(rounds=2)

    await engine.run()

    assert all(agent.turns_taken == 2 for agent in agents.values())
    # Each agent saw round 1 then round 2, in order.
    for agent in agents.values():
        assert [ctx.round for ctx in agent.seen_contexts] == [1, 2]


async def test_round_change_is_emitted_once_per_round_in_order() -> None:
    engine, recorder, _ = build_engine(rounds=3)

    await engine.run()

    rounds = [event.payload.round for event in recorder.of_type(RoundChangeMessage)]
    assert rounds == [1, 2, 3]
    assert all(
        event.payload.total_rounds == 3 for event in recorder.of_type(RoundChangeMessage)
    )


async def test_round_change_precedes_the_first_thought_of_that_round() -> None:
    engine, recorder, _ = build_engine(rounds=1)

    await engine.run()

    assert recorder.types.index("round_change") < recorder.types.index("thought")


async def test_the_configured_number_of_rounds_is_respected() -> None:
    engine, _, agents = build_engine(rounds=5)

    await engine.run()

    assert engine.round == 5
    assert agents[COOP].turns_taken == 5


# --------------------------------------------------------------------------- #
# The offer log
# --------------------------------------------------------------------------- #


async def test_an_offer_is_logged_pending_and_emitted() -> None:
    engine, recorder, _ = build_engine({COOP: [offer(MAXI, 10.0)]}, rounds=1)

    await engine.run()

    assert len(engine.offer_log) == 1
    record = engine.offer_log[0]
    assert (record.from_, record.to, record.amount, record.round) == (COOP, MAXI, 10.0, 1)
    assert record.accepted is None
    assert recorder.of_type(OfferMessage)[0].payload.from_ == COOP


async def test_accepting_stamps_the_existing_entry_without_appending() -> None:
    """Append-only means never removed or reordered; `accepted` is stamped once."""
    engine, _, _ = build_engine(
        {
            COOP: [offer(MAXI, 10.0)],
            MAXI: [AgentDecision(action="accept", target_offer_id="o1", thought="Mine now.")],
        },
        rounds=1,
    )

    await engine.run()

    assert len(engine.offer_log) == 1
    assert engine.offer_log[0].accepted is True


async def test_rejecting_stamps_false() -> None:
    engine, _, _ = build_engine(
        {
            COOP: [offer(MAXI, 10.0)],
            MAXI: [AgentDecision(action="reject", target_offer_id="o1", thought="No.")],
        },
        rounds=1,
    )

    await engine.run()

    assert engine.offer_log[0].accepted is False


async def test_log_order_is_stable_as_entries_are_answered() -> None:
    engine, _, _ = build_engine(
        {
            COOP: [offer(MAXI, 10.0), offer(TIT, 5.0)],
            MAXI: [
                AgentDecision(action="pass", thought="Later."),
                AgentDecision(action="accept", target_offer_id="o1", thought="Fine."),
            ],
        },
        rounds=2,
    )

    await engine.run()

    assert [(o.from_, o.to, o.amount) for o in engine.offer_log] == [
        (COOP, MAXI, 10.0),
        (COOP, TIT, 5.0),
    ]
    assert engine.offer_log[0].accepted is True
    assert engine.offer_log[1].accepted is None


async def test_every_turn_records_a_thought() -> None:
    engine, recorder, _ = build_engine(rounds=2)

    await engine.run()

    assert len(engine.thoughts) == 6  # 3 agents x 2 rounds
    assert len(recorder.of_type(ThoughtMessage)) == 6


# --------------------------------------------------------------------------- #
# Holdings
# --------------------------------------------------------------------------- #


async def test_acceptance_transfers_resource_between_parties() -> None:
    engine, _, _ = build_engine(
        {
            COOP: [offer(MAXI, 10.0)],
            MAXI: [AgentDecision(action="accept", target_offer_id="o1", thought="Yes.")],
        },
        rounds=1,
    )

    await engine.run()

    assert engine.holdings[COOP] == 15.0
    assert engine.holdings[MAXI] == 35.0


async def test_rejection_moves_no_resource() -> None:
    engine, _, _ = build_engine(
        {
            COOP: [offer(MAXI, 10.0)],
            MAXI: [AgentDecision(action="reject", target_offer_id="o1", thought="No.")],
        },
        rounds=1,
    )

    await engine.run()

    assert engine.holdings[COOP] == 25.0
    assert engine.holdings[MAXI] == 25.0


async def test_the_pool_total_is_conserved_across_a_long_random_game() -> None:
    agents = [RandomAgent(agent_id, seed=index) for index, agent_id in enumerate((COOP, MAXI, TIT))]
    engine = NegotiationEngine(
        session_id="random",
        agents=agents,
        settings=make_settings(rounds=6),
        emit=Recorder(),
    )

    await engine.run()

    assert sum(engine.holdings.values()) == pytest.approx(100.0)
    assert all(amount >= 0 for amount in engine.holdings.values())


async def test_an_agent_cannot_offer_more_than_it_holds() -> None:
    """An LLM may hallucinate a number; the offer is clamped, not dropped."""
    engine, _, _ = build_engine({COOP: [offer(MAXI, 10_000.0)]}, rounds=1)

    await engine.run()

    assert engine.offer_log[0].amount == 25.0


async def test_an_agent_offer_to_an_unknown_party_is_dropped_not_fatal() -> None:
    engine, _, _ = build_engine({COOP: [offer("ghost", 5.0)]}, rounds=1)

    await engine.run()

    assert engine.offer_log == []
    assert engine.finished is True  # the game carried on regardless


# --------------------------------------------------------------------------- #
# Answering offers
# --------------------------------------------------------------------------- #


async def test_an_agent_cannot_answer_an_offer_addressed_to_someone_else() -> None:
    engine, _, _ = build_engine(
        {
            COOP: [offer(MAXI, 10.0)],
            TIT: [AgentDecision(action="accept", target_offer_id="o1", thought="I'll take that.")],
        },
        rounds=1,
    )

    await engine.run()

    assert engine.offer_log[0].accepted is None
    assert engine.holdings[TIT] == 25.0


async def test_answering_an_unknown_offer_id_is_a_no_op() -> None:
    engine, _, _ = build_engine(
        {MAXI: [AgentDecision(action="accept", target_offer_id="nope", thought="Yes!")]},
        rounds=1,
    )

    await engine.run()

    assert engine.holdings[MAXI] == 25.0


async def test_an_answered_offer_leaves_the_pending_queue() -> None:
    engine, _, _ = build_engine(
        {
            COOP: [offer(MAXI, 10.0)],
            MAXI: [AgentDecision(action="accept", target_offer_id="o1", thought="Yes.")],
        },
        rounds=1,
    )

    await engine.run()

    assert engine.pending == {}


async def test_a_pending_offer_is_shown_to_its_recipient_and_nobody_else() -> None:
    engine, _, agents = build_engine({COOP: [offer(MAXI, 10.0)]}, rounds=2)

    await engine.run()

    maximizer_round_two = agents[MAXI].seen_contexts[1]
    titfortat_round_two = agents[TIT].seen_contexts[1]

    assert [o.from_id for o in maximizer_round_two.pending_offers] == [COOP]
    assert titfortat_round_two.pending_offers == []


# --------------------------------------------------------------------------- #
# Context handed to agents
# --------------------------------------------------------------------------- #


async def test_context_carries_holdings_parties_and_recent_log() -> None:
    engine, _, agents = build_engine({COOP: [offer(MAXI, 10.0)]}, rounds=2)

    await engine.run()
    context = agents[TIT].seen_contexts[1]

    assert context.pool.total == 100.0
    assert context.my_holdings == 25.0
    assert set(context.other_party_ids) == {COOP, MAXI, HUMAN_ID}
    assert [o.from_ for o in context.recent_offers] == [COOP]


async def test_context_carries_the_agents_own_trust_row() -> None:
    engine, _, agents = build_engine(rounds=1)

    await engine.run()
    context = agents[COOP].seen_contexts[0]

    assert set(context.trust_row) == {MAXI, TIT, HUMAN_ID}
    assert all(0.0 <= value <= 1.0 for value in context.trust_row.values())


async def test_reported_opponent_deltas_move_that_agents_private_trust() -> None:
    engine, _, _ = build_engine(
        {
            COOP: [
                AgentDecision(
                    action="pass",
                    thought="Watching.",
                    opponent_updates=[
                        OpponentDelta(agent_id=MAXI, trust_delta=-0.8, note="grabby")
                    ],
                )
            ]
        },
        rounds=1,
    )

    await engine.run()

    assert engine.beliefs[COOP].about(MAXI).trust_score < 0.5
    # Only the reporting agent's own beliefs move.
    assert engine.beliefs[TIT].about(MAXI).trust_score == 0.5


# --------------------------------------------------------------------------- #
# Human injection
# --------------------------------------------------------------------------- #


async def test_a_human_offer_becomes_pending_and_appears_in_the_returned_state() -> None:
    engine, recorder, _ = build_engine(rounds=1)

    state = await engine.inject_offer(
        OfferSchema(from_=HUMAN_ID, to=MAXI, resource="budget", amount=12.0)
    )

    assert [(o.from_, o.to, o.amount) for o in state.offer_log] == [(HUMAN_ID, MAXI, 12.0)]
    assert len(engine.pending) == 1
    assert recorder.types == ["offer", "graph_update"]


async def test_an_injected_offer_is_visible_to_the_next_agent_to_act() -> None:
    """The reaction requirement: agents must see a human offer on their next turn."""
    engine, _, agents = build_engine(rounds=1)

    await engine.inject_offer(
        OfferSchema(from_=HUMAN_ID, to=MAXI, resource="budget", amount=12.0)
    )
    await engine.run()

    assert [o.from_id for o in agents[MAXI].seen_contexts[0].pending_offers] == [HUMAN_ID]


async def test_an_agent_can_accept_a_human_offer() -> None:
    engine, _, _ = build_engine(
        {MAXI: [AgentDecision(action="accept", target_offer_id="o1", thought="Thanks.")]},
        rounds=1,
    )

    await engine.inject_offer(
        OfferSchema(from_=HUMAN_ID, to=MAXI, resource="budget", amount=12.0)
    )
    await engine.run()

    assert engine.holdings[HUMAN_ID] == 13.0
    assert engine.holdings[MAXI] == 37.0
    assert engine.offer_log[0].accepted is True


@pytest.mark.parametrize(
    ("to", "resource", "amount", "expected"),
    [
        ("ghost", "budget", 5.0, "unknown recipient"),
        (MAXI, "gold", 5.0, "unknown resource"),
        (MAXI, "budget", 0.0, "greater than zero"),
        (MAXI, "budget", -5.0, "greater than zero"),
        (MAXI, "budget", 500.0, "holds only"),
        (HUMAN_ID, "budget", 5.0, "offer to yourself"),
    ],
)
async def test_bad_human_offers_are_rejected_with_a_useful_message(
    to: str, resource: str, amount: float, expected: str
) -> None:
    """Humans get a real error; only agents get silent clamping."""
    engine, _, _ = build_engine(rounds=1)

    with pytest.raises(OfferRejected, match=expected):
        await engine.inject_offer(
            OfferSchema(from_=HUMAN_ID, to=to, resource=resource, amount=amount)
        )

    assert engine.offer_log == []


async def test_injecting_after_the_game_ends_is_rejected() -> None:
    engine, _, _ = build_engine(rounds=1)
    await engine.run()

    with pytest.raises(OfferRejected, match="already finished"):
        await engine.inject_offer(
            OfferSchema(from_=HUMAN_ID, to=MAXI, resource="budget", amount=5.0)
        )


# --------------------------------------------------------------------------- #
# The reveal
# --------------------------------------------------------------------------- #


async def test_objectives_stay_hidden_until_the_reveal() -> None:
    engine, _, _ = build_engine(rounds=1)

    assert engine.snapshot().closing_positions is None

    await engine.run()

    assert engine.snapshot().closing_positions is not None


async def test_reveal_is_emitted_last_and_is_well_formed() -> None:
    engine, recorder, _ = build_engine(rounds=2)

    await engine.run()

    assert recorder.types[-1] == "closing"
    payload = recorder.of_type(ClosingMessage)[0].payload
    assert set(payload.positions) == {COOP, MAXI, TIT}
    assert payload.final_state.holdings == engine.holdings
    assert payload.final_state.closing_positions == payload.positions


async def test_graph_updates_accompany_every_offer_event() -> None:
    engine, recorder, _ = build_engine(
        {
            COOP: [offer(MAXI, 10.0)],
            MAXI: [AgentDecision(action="accept", target_offer_id="o1", thought="Yes.")],
        },
        rounds=1,
    )

    await engine.run()

    reasons = [event.payload.reason for event in recorder.of_type(GraphUpdateMessage)]
    assert reasons == ["offer_made", "offer_accepted"]


# --------------------------------------------------------------------------- #
# Lifecycle
# --------------------------------------------------------------------------- #


async def test_start_runs_in_the_background_and_stop_unwinds_cleanly() -> None:
    engine, _, _ = build_engine(rounds=2)

    task = engine.start()
    await task

    assert engine.finished is True

    await engine.stop()  # idempotent on a completed game


async def test_a_session_cannot_be_started_twice() -> None:
    engine, _, _ = build_engine(rounds=1)
    engine.start()

    with pytest.raises(RuntimeError, match="already started"):
        engine.start()

    await engine.stop()


async def test_a_broken_listener_does_not_kill_the_negotiation() -> None:
    async def exploding_emitter(message: WSMessage) -> None:
        raise RuntimeError("client went away")

    engine = NegotiationEngine(
        session_id="lossy",
        agents=[ScriptedAgent(COOP, [offer(MAXI, 5.0)])],
        settings=make_settings(rounds=2),
        emit=exploding_emitter,
    )

    await engine.run()

    assert engine.finished is True
    assert engine.closing is not None
