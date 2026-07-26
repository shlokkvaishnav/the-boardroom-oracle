"""Turn order, and the fairness it must not break.

The chair is allowed to change *when* someone speaks in a round. It is not
allowed to change *whether* they do — an agent starved of turns by never being
mentioned would quietly drop out of a discussion it is meant to be part of, and
the round would stop costing one call per agent, which the budget's floor
arithmetic depends on.
"""

from __future__ import annotations

from app.engine.chair import mentioned_parties, next_speaker
from app.models.agent_io import AgentDecision

from tests.test_engine import COOP, MAXI, TIT, build_engine

NAMES = {COOP: "Ada", MAXI: "Rex", TIT: "Mira"}
SEATING = [COOP, MAXI, TIT]


def said(text: str) -> AgentDecision:
    return AgentDecision(action="pass", thought=text)


# --------------------------------------------------------------------------- #
# Reading who was named
# --------------------------------------------------------------------------- #


def test_a_party_is_found_by_display_name_or_by_id() -> None:
    assert mentioned_parties("Rex, that number is wrong.", NAMES) == [MAXI]
    assert mentioned_parties("maximizer is overreaching.", NAMES) == [MAXI]


def test_names_come_back_in_the_order_they_appear() -> None:
    text = "Mira agrees with me, but Rex does not."

    assert mentioned_parties(text, NAMES) == [TIT, MAXI]


def test_matching_is_case_insensitive() -> None:
    assert mentioned_parties("ADA has a point.", NAMES) == [COOP]


def test_a_name_inside_a_longer_word_is_not_a_mention() -> None:
    """Word boundaries, so Mira does not fire on Miranda."""
    assert mentioned_parties("Miranda rights and Rexall pharmacies.", NAMES) == []


def test_nobody_named_is_an_empty_list_not_a_guess() -> None:
    assert mentioned_parties("That costs too much and you know it.", NAMES) == []


# --------------------------------------------------------------------------- #
# Choosing
# --------------------------------------------------------------------------- #


def test_being_named_puts_you_next() -> None:
    chosen = next_speaker(
        SEATING, names=NAMES, last_remark="Mira, answer the question.", last_speaker=COOP
    )

    assert chosen == TIT, "not the seating-order default"


def test_naming_someone_who_already_spoke_falls_through() -> None:
    """They are not in `waiting`, so the rule simply does not apply."""
    chosen = next_speaker(
        [MAXI, TIT], names=NAMES, last_remark="Ada is right.", last_speaker=COOP
    )

    assert chosen == MAXI


def test_naming_yourself_does_not_buy_another_turn() -> None:
    chosen = next_speaker(
        SEATING, names=NAMES, last_remark="Rex has said his piece.", last_speaker=MAXI
    )

    assert chosen == COOP, "the speaker cannot summon themselves"


def test_the_first_name_mentioned_wins_when_several_are() -> None:
    chosen = next_speaker(
        SEATING, names=NAMES, last_remark="Mira and Rex both dodged that.", last_speaker=COOP
    )

    assert chosen == TIT


def test_an_unanswered_offer_is_a_question_too() -> None:
    chosen = next_speaker(
        SEATING, names=NAMES, last_remark="Nobody named here.", awaiting_answer=[TIT, MAXI]
    )

    assert chosen == TIT, "oldest unanswered offer first"


def test_being_named_outranks_holding_an_offer() -> None:
    chosen = next_speaker(
        SEATING,
        names=NAMES,
        last_remark="Rex, you cannot be serious.",
        last_speaker=COOP,
        awaiting_answer=[TIT],
    )

    assert chosen == MAXI


def test_with_nothing_to_go_on_it_is_seating_order() -> None:
    assert next_speaker(SEATING, names=NAMES) == COOP
    assert next_speaker([MAXI, TIT], names=NAMES) == MAXI


# --------------------------------------------------------------------------- #
# The engine, where fairness has to hold
# --------------------------------------------------------------------------- #


async def test_everyone_still_acts_exactly_once_per_round() -> None:
    """The invariant. Reordering must never become starving."""
    engine, _, agents = build_engine(
        {
            COOP: [said("Rex, that is nonsense.")] * 4,
            MAXI: [said("Rex, I stand by it.")] * 4,
            TIT: [said("Rex again, then.")] * 4,
        },
        rounds=4,
    )

    await engine.run()

    assert all(agent.turns_taken == 4 for agent in agents.values())
    assert len(engine.thoughts) == 12


async def test_a_named_agent_is_pulled_forward_in_the_round() -> None:
    engine, _, _ = build_engine(
        {COOP: [said("Mira, you have dodged this twice.")], MAXI: [said("Fine.")]},
        rounds=1,
    )

    await engine.run()

    order = [t.agent_id for t in engine.thoughts]
    assert order == [COOP, TIT, MAXI], "Mira answers before Rex takes his turn"


async def test_a_human_remark_can_call_on_someone() -> None:
    """Human remarks cost no turn, so this is how you put someone on the spot."""
    engine, _, _ = build_engine({}, rounds=1)
    await engine.add_remark("human", "Mira, what do you actually think?")

    await engine.run()

    assert [t.agent_id for t in engine.thoughts][:2] == ["human", TIT]


async def test_turning_the_chair_off_restores_strict_seating_order() -> None:
    engine, _, _ = build_engine(
        {COOP: [said("Mira, answer me.")], MAXI: [said("Fine.")], TIT: [said("Later.")]},
        rounds=1,
        enable_chair=False,
    )

    await engine.run()

    assert [t.agent_id for t in engine.thoughts] == SEATING


async def test_reordering_does_not_change_what_a_round_costs() -> None:
    """The budget's floor is calls-per-agent-per-round; order must not affect it."""
    engine, _, _ = build_engine(
        {agent: [said("Rex, again.")] * 3 for agent in SEATING}, rounds=3
    )

    await engine.run()

    assert engine.budget.spent == 3 * len(SEATING)
