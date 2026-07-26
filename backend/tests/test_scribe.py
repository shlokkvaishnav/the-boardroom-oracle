"""The scribe, and the three promises it has to keep.

The happy path — it proposes a link, the link lands — is the least interesting
thing here. What earns tests is everything the scribe is *not* allowed to do:
cost a turn, endanger the ending, or forge provenance.
"""

from __future__ import annotations

import asyncio

import pytest

from app.agents.scribe import ClaimLink, Scribe
from app.engine.knowledge_graph import KnowledgeGraph
from app.llm_client import LLMError
from app.models.agent_io import AgentDecision, Claim
from app.models.messages import KnowledgeUpdateMessage

from tests.test_engine import COOP, MAXI, TIT, build_engine

# --------------------------------------------------------------------------- #
# Doubles
# --------------------------------------------------------------------------- #


class FakeScribe:
    """Returns canned links and records what it was shown."""

    def __init__(self, links: list[ClaimLink] | None = None, error: Exception | None = None):
        self.links = links or []
        self.error = error
        self.calls: list[tuple[list[str], list[str]]] = []
        self.started = asyncio.Event()
        self.release: asyncio.Event | None = None

    async def read(self, *, new_claims, earlier_claims):
        self.calls.append(
            ([c.id for c in new_claims], [c.id for c in earlier_claims])
        )
        self.started.set()
        if self.release is not None:
            await self.release.wait()
        if self.error is not None:
            raise self.error
        return list(self.links)


class FakeLLM:
    """Stands in for LLMClient at the boundary the real Scribe talks to."""

    def __init__(self, payload: object = None, error: Exception | None = None):
        self.payload = payload
        self.error = error
        self.models: list[str | None] = []

    async def generate_structured(self, prompt, schema, *, system=None, model=None):
        self.models.append(model)
        if self.error is not None:
            raise self.error
        return self.payload


def claim(text: str, kind: str = "fact") -> Claim:
    return Claim(text=text, kind=kind)


def speak(text: str, *claims: Claim) -> AgentDecision:
    return AgentDecision(action="pass", thought=text, claims=list(claims))


def make_settings_scribe(payload, **overrides):
    from app.config import Settings

    base = {"_env_file": None, **overrides}
    return Scribe(FakeLLM(payload), Settings(**base))  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# The Scribe itself
# --------------------------------------------------------------------------- #


async def test_no_new_claims_means_no_call_at_all() -> None:
    llm = FakeLLM({"links": []})
    from app.config import Settings

    scribe = Scribe(llm, Settings(_env_file=None))  # type: ignore[arg-type]

    assert await scribe.read(new_claims=[], earlier_claims=[]) == []
    assert llm.models == [], "an empty round must not reach the provider"


async def test_a_provider_failure_costs_the_links_not_the_session() -> None:
    from app.config import Settings

    graph = KnowledgeGraph([(COOP, "Ada")])
    cid, _ = graph.add_claim(author_id=COOP, text="A.", claim_kind="fact", round=1)
    scribe = Scribe(FakeLLM(error=LLMError("upstream is down")), Settings(_env_file=None))  # type: ignore[arg-type]

    links = await scribe.read(new_claims=[graph.node(cid)], earlier_claims=[])

    assert links == []


async def test_a_malformed_reading_is_dropped_rather_than_raised() -> None:
    from app.config import Settings

    graph = KnowledgeGraph([(COOP, "Ada")])
    cid, _ = graph.add_claim(author_id=COOP, text="A.", claim_kind="fact", round=1)
    scribe = Scribe(FakeLLM({"links": "not a list"}), Settings(_env_file=None))  # type: ignore[arg-type]

    assert await scribe.read(new_claims=[graph.node(cid)], earlier_claims=[]) == []


async def test_links_naming_claims_that_do_not_exist_are_discarded() -> None:
    from app.config import Settings

    graph = KnowledgeGraph([(COOP, "Ada")])
    first, _ = graph.add_claim(author_id=COOP, text="A.", claim_kind="fact", round=1)
    payload = {
        "links": [
            {"source_claim": first, "target_claim": "c999", "kind": "contradicts"},
            {"source_claim": first, "target_claim": first, "kind": "supports"},
        ]
    }
    scribe = Scribe(FakeLLM(payload), Settings(_env_file=None))  # type: ignore[arg-type]

    links = await scribe.read(new_claims=[graph.node(first)], earlier_claims=[])

    assert links == [], "an unknown target and a self-link are both refused"


async def test_the_scribe_runs_on_the_cheap_model() -> None:
    from app.config import Settings

    llm = FakeLLM({"links": []})
    graph = KnowledgeGraph([(COOP, "Ada")])
    cid, _ = graph.add_claim(author_id=COOP, text="A.", claim_kind="fact", round=1)
    settings = Settings(_env_file=None, scribe_model="tiny-model")  # type: ignore[arg-type]

    await Scribe(llm, settings).read(new_claims=[graph.node(cid)], earlier_claims=[])

    assert llm.models == ["tiny-model"]


# --------------------------------------------------------------------------- #
# The engine running it
# --------------------------------------------------------------------------- #


async def test_a_link_the_scribe_finds_lands_on_the_graph_and_the_wire() -> None:
    scribe = FakeScribe(
        [ClaimLink(source_claim="c2", target_claim="c1", kind="contradicts")]
    )
    engine, recorder, _ = build_engine(
        {
            COOP: [speak("It's 330.", claim("The deficit is 330,000 tons."))],
            MAXI: [speak("It's 150.", claim("The deficit is 150,000 tons."))],
        },
        rounds=1,
        scribe=scribe,
    )

    await engine.run()

    edges = [e for e in engine.knowledge.view().edges if e.kind == "contradicts"]
    assert len(edges) == 1
    assert (edges[0].source, edges[0].target) == ("c2", "c1")

    frames = [
        f for f in recorder.of_type(KnowledgeUpdateMessage) if f.payload.reason == "scribe"
    ]
    assert len(frames) == 1


async def test_the_scribe_never_sits_in_a_turn() -> None:
    """The constraint that matters. A blocked scribe must not stall the game."""
    scribe = FakeScribe([])
    scribe.release = asyncio.Event()  # never set: the pass hangs forever

    engine, recorder, agents = build_engine(
        {agent: [speak("A point.", claim("A claim."))] * 3 for agent in (COOP, MAXI, TIT)},
        rounds=3,
        scribe=scribe,
        scribe_settle_timeout_seconds=0.2,
    )

    # A hung scribe would deadlock this if the pass were awaited in the loop.
    # The short settle timeout only bounds the *ending*; if the pass were in the
    # turn path, three rounds would never complete at all.
    await asyncio.wait_for(engine.run(), timeout=5.0)

    assert engine.round == 3, "every round played"
    assert recorder.types[-1] == "closing", "and the session still ended"
    assert agents[COOP].turns_taken == 3


async def test_each_pass_reads_only_the_claims_it_has_not_seen() -> None:
    scribe = FakeScribe([])
    engine, _, _ = build_engine(
        {COOP: [speak("One.", claim("First.")), speak("Two.", claim("Second."))]},
        rounds=2,
        scribe=scribe,
    )

    await engine.run()

    assert len(scribe.calls) == 2
    assert scribe.calls[0] == (["c1"], [])
    # Round two sees only its own claim as new, with round one's as context.
    assert scribe.calls[1] == (["c2"], ["c1"])


async def test_a_round_where_nobody_argued_costs_no_call() -> None:
    scribe = FakeScribe([])
    engine, _, _ = build_engine({COOP: [speak("Fine by me.")]}, rounds=2, scribe=scribe)

    await engine.run()

    assert scribe.calls == []


async def test_the_scribe_is_refused_when_only_the_floor_is_left() -> None:
    """Enrichment yields to finishing, exactly as search does."""
    scribe = FakeScribe([])
    engine, _, _ = build_engine(
        {agent: [speak("A point.", claim("A claim."))] * 4 for agent in (COOP, MAXI, TIT)},
        rounds=4,
        session_call_budget=12,  # buys the four rounds and nothing more
        scribe=scribe,
    )

    await engine.run()

    assert scribe.calls == []
    assert engine.round == 4
    assert not engine.ended_early


async def test_stopping_a_session_cancels_a_pass_in_flight() -> None:
    scribe = FakeScribe([])
    scribe.release = asyncio.Event()

    engine, _, _ = build_engine(
        {COOP: [speak("A point.", claim("A claim."))] * 6},
        rounds=6,
        turn_delay_seconds=0.05,
        scribe=scribe,
    )
    engine.start()
    await asyncio.wait_for(scribe.started.wait(), timeout=5.0)

    await engine.stop()

    assert not [t for t in engine._scribe_tasks if not t.done()]


async def test_the_closing_snapshot_waits_for_a_pass_still_running() -> None:
    """A link found as the last round closed belongs in the final state."""
    scribe = FakeScribe(
        [ClaimLink(source_claim="c2", target_claim="c1", kind="supports")]
    )
    scribe.release = asyncio.Event()

    engine, recorder, _ = build_engine(
        {
            COOP: [speak("One.", claim("First."))],
            MAXI: [speak("Two.", claim("Second."))],
        },
        rounds=1,
        scribe=scribe,
    )

    async def unblock_once_started() -> None:
        await scribe.started.wait()
        scribe.release.set()  # type: ignore[union-attr]

    await asyncio.gather(engine.run(), unblock_once_started())

    closing = engine.closing
    assert closing is not None
    kinds = [e.kind for e in closing.final_state.knowledge_graph.edges]
    assert "supports" in kinds, "the closing snapshot carries the late link"


async def test_a_scribe_that_raises_does_not_disturb_the_discussion() -> None:
    scribe = FakeScribe(error=RuntimeError("scribe exploded"))
    engine, recorder, _ = build_engine(
        {COOP: [speak("A point.", claim("A claim."))] * 2},
        rounds=2,
        scribe=scribe,
    )

    await engine.run()

    assert engine.round == 2
    assert recorder.types[-1] == "closing"
    assert not [
        f for f in recorder.of_type(KnowledgeUpdateMessage) if f.payload.reason == "scribe"
    ]


@pytest.mark.parametrize("forged", ["asserts", "cites", "about"])
async def test_a_scribe_cannot_forge_provenance_through_the_engine(forged: str) -> None:
    """Defence in depth: the graph refuses, even if a link got this far."""
    scribe = FakeScribe(
        [ClaimLink.model_construct(source_claim="c2", target_claim="c1", kind=forged)]
    )
    engine, _, _ = build_engine(
        {
            COOP: [speak("One.", claim("First."))],
            MAXI: [speak("Two.", claim("Second."))],
        },
        rounds=1,
        scribe=scribe,
    )

    await engine.run()

    assert not [e for e in engine.knowledge.view().edges if e.kind == forged and e.source == "c2"]
