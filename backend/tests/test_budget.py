"""The call budget, and the promise it exists to keep.

The property under test is not "the budget counts correctly" — that part is
arithmetic. It is that **a session always reaches its closing frame**, however
badly the allowance is set. A discussion that stops in round four and never ends
is the one outcome the demo cannot survive, so it gets its own test file.
"""

from __future__ import annotations

import pytest

from app.agents.base import TurnContext
from app.engine.budget import CallBudget
from app.models.messages import ClosingMessage

from tests.test_engine import COOP, MAXI, TIT, build_engine, offer

# --------------------------------------------------------------------------- #
# The type itself
# --------------------------------------------------------------------------- #


def test_an_unbounded_budget_never_refuses_anything() -> None:
    budget = CallBudget(None)
    budget.spend(10_000)

    assert budget.unlimited
    assert budget.can_afford(10_000)
    assert budget.can_afford_extra(floor=10_000, extra=10_000)
    # Still counts, so the number remains worth logging.
    assert budget.spent == 10_000


def test_the_floor_is_reserved_before_any_extra_is_granted() -> None:
    budget = CallBudget(20)
    budget.spend(8)  # 12 left

    # Finishing costs 10, so there is room for 2 more on top — not 3.
    assert budget.can_afford_extra(floor=10, extra=2)
    assert not budget.can_afford_extra(floor=10, extra=3)
    # ...even though 3 more calls would themselves fit comfortably.
    assert budget.can_afford(3)


def test_remaining_never_goes_negative() -> None:
    budget = CallBudget(5)
    budget.spend(9)

    assert budget.remaining == 0
    assert not budget.can_afford(1)


# --------------------------------------------------------------------------- #
# The engine honouring it
# --------------------------------------------------------------------------- #


async def test_a_normal_session_never_touches_the_default_budget() -> None:
    """The ceiling is for runaway, not for ordinary play."""
    engine, recorder, _ = build_engine(
        {agent: [offer(MAXI, 5.0)] * 6 for agent in (COOP, MAXI, TIT)},
        rounds=6,
    )

    await engine.run()

    assert not engine.ended_early
    assert engine.round == 6
    assert recorder.types[-1] == "closing"
    assert engine.budget.spent < (engine.budget.total or 0)


async def test_a_session_that_runs_out_still_emits_its_closing() -> None:
    """The whole point. A cut-short discussion still gets an ending."""
    engine, recorder, _ = build_engine(
        {agent: [offer(MAXI, 5.0)] * 8 for agent in (COOP, MAXI, TIT)},
        rounds=8,
        # Three agents a round, so this pays for two rounds and no more.
        session_call_budget=6,
    )

    await engine.run()

    assert engine.ended_early
    assert engine.round < 8
    assert recorder.types[-1] == "closing"
    assert engine.finished

    closing = recorder.of_type(ClosingMessage)[0].payload
    assert closing.positions, "an early ending still reports where parties stood"
    assert sum(closing.final_state.holdings.values()) == pytest.approx(100.0)


async def test_search_is_switched_off_before_finishing_is_put_at_risk() -> None:
    """Enrichment yields to reaching the end, not the other way round."""
    contexts: list[TurnContext] = []

    engine, _, agents = build_engine(
        {agent: [offer(MAXI, 1.0)] * 4 for agent in (COOP, MAXI, TIT)},
        rounds=4,
        # 12 calls buys the four rounds exactly, with nothing spare for probes.
        session_call_budget=12,
    )
    original = agents[COOP].decide

    async def spy(context: TurnContext):
        contexts.append(context)
        return await original(context)

    agents[COOP].decide = spy  # type: ignore[method-assign]

    await engine.run()

    assert contexts, "the spy saw at least one turn"
    assert all(not context.allow_search for context in contexts)
    assert engine.round == 4
    assert not engine.ended_early


async def test_search_stays_on_while_there_is_surplus() -> None:
    contexts: list[TurnContext] = []

    engine, _, agents = build_engine(
        {agent: [offer(TIT, 1.0)] * 2 for agent in (COOP, MAXI, TIT)},
        rounds=2,
        session_call_budget=60,
    )
    original = agents[COOP].decide

    async def spy(context: TurnContext):
        contexts.append(context)
        return await original(context)

    agents[COOP].decide = spy  # type: ignore[method-assign]

    await engine.run()

    assert contexts
    assert all(context.allow_search for context in contexts)


async def test_an_unlimited_budget_reproduces_the_old_behaviour() -> None:
    engine, recorder, _ = build_engine(
        {agent: [offer(MAXI, 5.0)] * 3 for agent in (COOP, MAXI, TIT)},
        rounds=3,
        session_call_budget=0,
    )

    await engine.run()

    assert engine.budget.unlimited
    assert not engine.ended_early
    assert engine.round == 3
    assert recorder.types[-1] == "closing"
