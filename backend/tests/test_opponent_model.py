"""Milestone 6: beliefs adapt, and adapt in the right direction.

The headline requirement is the rejection test: after an offer is turned down,
the offerer's read on that party must move the *unfavourable* way. These are
pure-function tests plus a couple of end-to-end checks through the engine, so
they run without an API key.
"""

from __future__ import annotations

import pytest

from app.agents.mock_agent import ScriptedAgent
from app.agents.opponent_model import ALPHA, BeliefSet, OpponentBelief, ema
from app.agents.personas import HUMAN_ID
from app.config import Settings
from app.engine.negotiation import NegotiationEngine
from app.models.agent_io import AgentDecision, ProposedOffer

COOP, MAXI, TIT = "cooperator", "maximizer", "titfortat"
PARTIES = [COOP, MAXI, TIT, HUMAN_ID]


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


def offer(to: str, amount: float) -> AgentDecision:
    return AgentDecision(
        action="offer",
        offer=ProposedOffer(to=to, resource="budget", amount=amount),
        thought="offering",
    )


# --------------------------------------------------------------------------- #
# The EMA helper
# --------------------------------------------------------------------------- #


def test_ema_moves_toward_the_observation_without_reaching_it() -> None:
    assert ema(0.5, 1.0) == pytest.approx(0.5 + ALPHA * 0.5)
    assert ema(0.5, 0.0) == pytest.approx(0.5 - ALPHA * 0.5)


def test_ema_converges_on_a_repeated_observation() -> None:
    value = 0.5
    for _ in range(30):
        value = ema(value, 1.0)

    assert value == pytest.approx(1.0, abs=1e-4)


def test_ema_never_leaves_the_span_of_its_inputs() -> None:
    assert 0.0 <= ema(0.0, 1.0) <= 1.0
    assert 0.0 <= ema(1.0, 0.0) <= 1.0


# --------------------------------------------------------------------------- #
# Direction of movement
# --------------------------------------------------------------------------- #


def test_acceptance_raises_the_perceived_acceptance_rate() -> None:
    belief = OpponentBelief(agent_id=MAXI)

    belief.observe_response_to_my_offer(accepted=True)

    assert belief.acceptance_rate > 0.5
    assert (belief.offers_i_made, belief.offers_they_accepted) == (1, 1)


def test_rejection_lowers_the_perceived_acceptance_rate() -> None:
    belief = OpponentBelief(agent_id=MAXI)

    belief.observe_response_to_my_offer(accepted=False)

    assert belief.acceptance_rate < 0.5
    assert (belief.offers_i_made, belief.offers_they_accepted) == (1, 0)


def test_a_generous_offer_reads_as_less_aggressive() -> None:
    belief = OpponentBelief(agent_id=MAXI)

    belief.observe_their_offer(favorability=0.9)

    assert belief.perceived_aggressiveness < 0.5


def test_a_stingy_offer_reads_as_more_aggressive() -> None:
    belief = OpponentBelief(agent_id=MAXI)

    belief.observe_their_offer(favorability=0.02)

    assert belief.perceived_aggressiveness > 0.5


def test_repeated_rejections_drive_the_acceptance_rate_toward_zero() -> None:
    belief = OpponentBelief(agent_id=MAXI)

    for _ in range(10):
        belief.observe_response_to_my_offer(accepted=False)

    assert belief.acceptance_rate < 0.01
    assert belief.acceptance_rate >= 0.0


def test_the_most_recent_behaviour_dominates() -> None:
    """Recency is the point of an EMA: a change of heart shows up fast."""
    belief = OpponentBelief(agent_id=MAXI)
    for _ in range(5):
        belief.observe_response_to_my_offer(accepted=True)
    peak = belief.acceptance_rate

    for _ in range(2):
        belief.observe_response_to_my_offer(accepted=False)

    assert belief.acceptance_rate < peak * 0.6


@pytest.mark.parametrize(
    ("delta", "direction"),
    [(0.6, 1), (-0.6, -1)],
)
def test_self_reported_deltas_move_private_trust_the_way_they_point(
    delta: float, direction: int
) -> None:
    belief = OpponentBelief(agent_id=MAXI)

    belief.apply_reported_delta(delta)

    assert (belief.trust_score - 0.5) * direction > 0


def test_private_trust_is_clamped_to_the_unit_interval() -> None:
    belief = OpponentBelief(agent_id=MAXI)

    for _ in range(20):
        belief.apply_reported_delta(-1.0)
    assert belief.trust_score == 0.0

    for _ in range(40):
        belief.apply_reported_delta(1.0)
    assert belief.trust_score == 1.0


def test_a_single_dramatic_self_report_cannot_swing_trust_end_to_end() -> None:
    belief = OpponentBelief(agent_id=MAXI)

    belief.apply_reported_delta(-1.0)

    assert belief.trust_score == 0.0 or belief.trust_score > 0.0
    assert belief.trust_score == pytest.approx(0.0)  # 0.5 - 0.5 lands exactly at the floor


# --------------------------------------------------------------------------- #
# Rendering into the prompt
# --------------------------------------------------------------------------- #


def test_a_belief_set_covers_everyone_except_its_owner() -> None:
    beliefs = BeliefSet.for_agent(COOP, PARTIES)

    assert set(beliefs.beliefs) == {MAXI, TIT, HUMAN_ID}


def test_render_shows_both_the_private_and_public_trust_numbers() -> None:
    beliefs = BeliefSet.for_agent(COOP, PARTIES)
    beliefs.about(MAXI).apply_reported_delta(-0.4)

    rendered = beliefs.render({MAXI: 0.8, TIT: 0.5, HUMAN_ID: 0.5})

    assert "my_trust=0.30" in rendered
    assert "public_trust=0.80" in rendered


def test_render_is_stable_and_lists_every_party() -> None:
    beliefs = BeliefSet.for_agent(COOP, PARTIES)

    lines = beliefs.render().splitlines()

    assert len(lines) == 3
    assert lines == sorted(lines)  # deterministic ordering


def test_render_handles_an_empty_belief_set() -> None:
    assert BeliefSet(owner_id=COOP).render() == "(no reads on anyone yet)"


# --------------------------------------------------------------------------- #
# Through the engine — the requirement, end to end
# --------------------------------------------------------------------------- #


async def test_a_rejected_offer_lowers_the_offerers_read_on_the_rejecter() -> None:
    """The headline check: rejection must move belief the unfavourable way."""
    engine = NegotiationEngine(
        session_id="reject",
        agents=[
            ScriptedAgent(COOP, [offer(MAXI, 20.0)]),
            ScriptedAgent(
                MAXI, [AgentDecision(action="reject", target_offer_id="o1", thought="No.")]
            ),
            ScriptedAgent(TIT, []),
        ],
        settings=make_settings(rounds=1),
    )
    before = engine.beliefs[COOP].about(MAXI).acceptance_rate

    await engine.run()
    after = engine.beliefs[COOP].about(MAXI)

    assert after.acceptance_rate < before
    assert after.offers_i_made == 1
    assert after.offers_they_accepted == 0
    # And the public record moved the same way.
    assert engine.graph.weight(COOP, MAXI) < 0.5


async def test_an_accepted_offer_raises_the_offerers_read_on_the_accepter() -> None:
    engine = NegotiationEngine(
        session_id="accept",
        agents=[
            ScriptedAgent(COOP, [offer(MAXI, 20.0)]),
            ScriptedAgent(
                MAXI, [AgentDecision(action="accept", target_offer_id="o1", thought="Yes.")]
            ),
            ScriptedAgent(TIT, []),
        ],
        settings=make_settings(rounds=1),
    )

    await engine.run()
    after = engine.beliefs[COOP].about(MAXI)

    assert after.acceptance_rate > 0.5
    assert after.offers_they_accepted == 1
    assert engine.graph.weight(COOP, MAXI) > 0.5


async def test_receiving_a_stingy_offer_raises_perceived_aggressiveness() -> None:
    engine = NegotiationEngine(
        session_id="stingy",
        agents=[
            ScriptedAgent(COOP, []),
            ScriptedAgent(MAXI, [offer(COOP, 1.0)]),
            ScriptedAgent(TIT, []),
        ],
        settings=make_settings(rounds=1),
    )

    await engine.run()

    assert engine.beliefs[COOP].about(MAXI).perceived_aggressiveness > 0.5
    assert engine.beliefs[COOP].about(MAXI).offers_they_made_me == 1


async def test_beliefs_visibly_diverge_over_successive_rounds() -> None:
    """What the audience should see: the same agent read differently over time."""
    engine = NegotiationEngine(
        session_id="diverge",
        agents=[
            ScriptedAgent(COOP, [offer(MAXI, 10.0), offer(TIT, 10.0), offer(MAXI, 10.0)]),
            ScriptedAgent(
                MAXI,
                [
                    AgentDecision(action="reject", target_offer_id="o1", thought="No."),
                    AgentDecision(action="pass", thought="..."),
                    AgentDecision(action="reject", target_offer_id="o3", thought="Still no."),
                ],
            ),
            ScriptedAgent(
                TIT,
                [
                    AgentDecision(action="pass", thought="..."),
                    AgentDecision(action="accept", target_offer_id="o2", thought="Thanks."),
                    AgentDecision(action="pass", thought="..."),
                ],
            ),
        ],
        settings=make_settings(rounds=3),
    )

    await engine.run()
    coop_view = engine.beliefs[COOP]

    # Ada has learned that Rex refuses and Mira accepts.
    assert coop_view.about(MAXI).acceptance_rate < 0.2
    assert coop_view.about(TIT).acceptance_rate > 0.6
    assert coop_view.about(TIT).acceptance_rate > coop_view.about(MAXI).acceptance_rate


async def test_each_agent_keeps_its_own_private_view() -> None:
    engine = NegotiationEngine(
        session_id="private",
        agents=[
            ScriptedAgent(COOP, [offer(MAXI, 20.0)]),
            ScriptedAgent(
                MAXI, [AgentDecision(action="reject", target_offer_id="o1", thought="No.")]
            ),
            ScriptedAgent(TIT, []),
        ],
        settings=make_settings(rounds=1),
    )

    await engine.run()

    # Ada was rebuffed; Mira, who wasn't involved, learned nothing about Rex's
    # willingness to accept *her* offers.
    assert engine.beliefs[COOP].about(MAXI).acceptance_rate < 0.5
    assert engine.beliefs[TIT].about(MAXI).acceptance_rate == 0.5
