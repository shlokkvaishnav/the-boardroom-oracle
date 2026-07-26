"""The closing report, and the promise that the closing happens regardless.

The synthesis is enrichment. The tests that matter are the ones proving a
session still ends — with positions, on screen — when it cannot run.
"""

from __future__ import annotations

from app.agents.rapporteur import Rapporteur
from app.config import Settings
from app.llm_client import LLMError
from app.models.agent_io import AgentDecision, Claim
from app.models.messages import ClosingMessage
from app.models.schemas import AgentInfo, AgentThought

from tests.test_engine import COOP, MAXI, TIT, build_engine


class FakeLLM:
    def __init__(self, payload=None, error: Exception | None = None):
        self.payload = payload
        self.error = error
        self.calls = 0

    async def generate_structured(self, prompt, schema, *, system=None, model=None):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.payload


class FakeRapporteur:
    def __init__(self, synthesis=None, error: Exception | None = None):
        self.synthesis = synthesis
        self.error = error
        self.calls = 0

    async def summarise(self, *, topic, parties, remarks, claims):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.synthesis


def say(text: str, *claims: Claim) -> AgentDecision:
    return AgentDecision(action="pass", thought=text, claims=list(claims))


def parties() -> list[AgentInfo]:
    return [
        AgentInfo(id=COOP, name="Ada", persona="Cooperator", color="#0f0", is_human=False),
        AgentInfo(id=MAXI, name="Rex", persona="Maximizer", color="#f00", is_human=False),
    ]


def remarks() -> list[AgentThought]:
    return [
        AgentThought(agent_id=COOP, text="Rationing hurts the smallest buyers most."),
        AgentThought(agent_id=MAXI, text="The deficit is smaller than Ada claims."),
    ]


def settings(**over) -> Settings:
    return Settings(_env_file=None, **over)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# The Rapporteur
# --------------------------------------------------------------------------- #


async def test_an_empty_discussion_is_not_worth_a_call() -> None:
    llm = FakeLLM({"statements": [], "agreed": [], "unresolved": []})

    result = await Rapporteur(llm, settings()).summarise(  # type: ignore[arg-type]
        topic="anything", parties=parties(), remarks=[], claims=[]
    )

    assert result is None
    assert llm.calls == 0


async def test_a_failed_call_returns_none_so_the_caller_can_fall_back() -> None:
    """None, not an empty synthesis — the caller must tell them apart."""
    llm = FakeLLM(error=LLMError("upstream down"))

    result = await Rapporteur(llm, settings()).summarise(  # type: ignore[arg-type]
        topic="t", parties=parties(), remarks=remarks(), claims=[]
    )

    assert result is None


async def test_agreeing_on_nothing_is_a_result_not_a_failure() -> None:
    """An empty `agreed` from a real report must survive as a real report."""
    llm = FakeLLM(
        {
            "statements": [{"agent_id": COOP, "position": "Protect small buyers."}],
            "agreed": [],
            "unresolved": ["the size of the deficit"],
        }
    )

    result = await Rapporteur(llm, settings()).summarise(  # type: ignore[arg-type]
        topic="t", parties=parties(), remarks=remarks(), claims=[]
    )

    assert result is not None, "distinct from a failure, which returns None"
    assert result.agreed == []
    assert result.unresolved == ["the size of the deficit"]


async def test_a_position_attributed_to_nobody_at_the_table_is_dropped() -> None:
    """The one thing worth refusing: invented words in a named mouth."""
    llm = FakeLLM(
        {
            "statements": [
                {"agent_id": COOP, "position": "Real."},
                {"agent_id": "ghost", "position": "Never said by anyone."},
            ],
            "agreed": [],
            "unresolved": [],
        }
    )

    result = await Rapporteur(llm, settings()).summarise(  # type: ignore[arg-type]
        topic="t", parties=parties(), remarks=remarks(), claims=[]
    )

    assert result is not None
    assert [s.agent_id for s in result.statements] == [COOP]


# --------------------------------------------------------------------------- #
# The engine using it
# --------------------------------------------------------------------------- #


async def test_the_report_becomes_the_closing_positions() -> None:
    from app.agents.rapporteur import ClosingStatement, RoomSynthesis

    rapporteur = FakeRapporteur(
        RoomSynthesis(
            statements=[
                ClosingStatement(agent_id=COOP, position="Protect the smallest buyers."),
                ClosingStatement(agent_id=MAXI, position="Ration by return on capital."),
            ],
            agreed=["the deficit is real"],
            unresolved=["how big it is"],
        )
    )
    engine, recorder, _ = build_engine(
        {COOP: [say("A point.")], MAXI: [say("Another.")]},
        rounds=1,
        rapporteur=rapporteur,
    )

    await engine.run()

    closing = recorder.of_type(ClosingMessage)[0].payload
    assert closing.synthesised is True
    assert closing.positions[COOP] == "Protect the smallest buyers."
    assert closing.agreed == ["the deficit is real"]
    assert closing.unresolved == ["how big it is"]
    # The snapshot agrees with the payload, as it always has.
    assert closing.final_state.closing_positions == closing.positions


async def test_without_a_rapporteur_the_old_last_remark_rule_still_ends_it() -> None:
    engine, recorder, _ = build_engine(
        {COOP: [say("First."), say("My last word.")]}, rounds=2
    )

    await engine.run()

    closing = recorder.of_type(ClosingMessage)[0].payload
    assert closing.synthesised is False
    assert closing.positions[COOP] == "My last word."
    assert closing.agreed == []


async def test_a_rapporteur_that_raises_does_not_cost_the_closing() -> None:
    rapporteur = FakeRapporteur(error=RuntimeError("exploded"))
    engine, recorder, _ = build_engine(
        {COOP: [say("Something said.")]}, rounds=1, rapporteur=rapporteur
    )

    await engine.run()

    assert recorder.types[-1] == "closing"
    closing = recorder.of_type(ClosingMessage)[0].payload
    assert closing.synthesised is False
    assert closing.positions[COOP] == "Something said."


async def test_no_budget_left_means_no_report_but_still_an_ending() -> None:
    rapporteur = FakeRapporteur()
    engine, recorder, _ = build_engine(
        {agent: [say("A point.")] * 2 for agent in (COOP, MAXI, TIT)},
        rounds=2,
        session_call_budget=6,  # exactly the two rounds, nothing spare
        rapporteur=rapporteur,
    )

    await engine.run()

    assert rapporteur.calls == 0
    assert recorder.types[-1] == "closing"
    assert recorder.of_type(ClosingMessage)[0].payload.positions


async def test_disabling_synthesis_reverts_to_the_old_ending_exactly() -> None:
    rapporteur = FakeRapporteur()
    engine, recorder, _ = build_engine(
        {COOP: [say("Last thing.")]},
        rounds=1,
        enable_synthesis=False,
        rapporteur=rapporteur,
    )

    await engine.run()

    assert rapporteur.calls == 0
    assert recorder.of_type(ClosingMessage)[0].payload.positions[COOP] == "Last thing."
