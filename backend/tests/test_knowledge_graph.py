"""The knowledge graph's rules, with no engine and no LLM involved.

The interesting assertions here are the *refusals*. This graph's whole value is
that its provenance is real, so the tests that matter most are the ones proving
it declines to record things nobody is entitled to say.
"""

from __future__ import annotations

from app.engine.knowledge_graph import KnowledgeGraph, entity_key
from app.models.agent_io import AgentDecision, Claim, TurnDecision
from app.models.messages import KnowledgeUpdateMessage
from app.models.schemas import SearchRecord

from tests.test_engine import build_engine

COOP, MAXI = "cooperator", "maximizer"


def build() -> KnowledgeGraph:
    return KnowledgeGraph([(COOP, "Ada"), (MAXI, "Rex")])


# --------------------------------------------------------------------------- #
# Identity and merging
# --------------------------------------------------------------------------- #


def test_parties_share_node_ids_with_the_trust_graph() -> None:
    graph = build()

    assert COOP in graph
    assert graph.node(COOP).kind == "party"
    assert graph.node(COOP).label == "Ada"


def test_the_same_entity_named_twice_is_one_node() -> None:
    graph = build()

    graph.add_claim(
        author_id=COOP, text="Chile's output is down.", claim_kind="fact",
        round=1, entities=["Chile"],
    )
    graph.add_claim(
        author_id=MAXI, text="Chile will recover by spring.", claim_kind="prediction",
        round=2, entities=["chile,"],
    )

    view = graph.view()
    entities = [n for n in view.nodes if n.kind == "entity"]
    assert len(entities) == 1, "case and punctuation must not fork the node"
    # Two claims point at the one entity — the reason to draw this as a graph.
    about = [e for e in view.edges if e.kind == "about"]
    assert len(about) == 2
    assert {e.target for e in about} == {entity_key("Chile")}


def test_one_source_cited_twice_is_one_evidence_node() -> None:
    graph = build()
    first, _ = graph.add_claim(
        author_id=COOP, text="Output fell 12%.", claim_kind="fact", round=1
    )
    second, _ = graph.add_claim(
        author_id=MAXI, text="The fall was temporary.", claim_kind="value", round=1
    )

    graph.add_evidence(claim_id=first, snippet="Output fell", source_url="http://x/a")
    graph.add_evidence(claim_id=second, snippet="Output fell", source_url="http://x/a")

    view = graph.view()
    assert len([n for n in view.nodes if n.kind == "evidence"]) == 1
    assert len([e for e in view.edges if e.kind == "cites"]) == 2


# --------------------------------------------------------------------------- #
# What gets recorded
# --------------------------------------------------------------------------- #


def test_a_claim_records_its_author_round_and_kind() -> None:
    graph = build()

    claim_id, delta = graph.add_claim(
        author_id=COOP,
        text="Rushing the deadline costs more later.",
        claim_kind="prediction",
        round=3,
        entities=["deadline"],
    )

    node = graph.node(claim_id)
    assert node.author_id == COOP
    assert node.round == 3
    assert node.claim_kind == "prediction"
    assert any(e.kind == "asserts" and e.source == COOP for e in delta.edges)


def test_a_long_claim_is_truncated_for_the_label_only() -> None:
    graph = build()
    long_text = "x" * 400

    claim_id, _ = graph.add_claim(
        author_id=COOP, text=long_text, claim_kind="value", round=1
    )

    assert len(graph.node(claim_id).label) < 200
    assert graph.node(claim_id).label.endswith("…")


def test_a_delta_reports_only_what_was_new() -> None:
    graph = build()
    graph.add_claim(
        author_id=COOP, text="One.", claim_kind="fact", round=1, entities=["Chile"]
    )

    _, delta = graph.add_claim(
        author_id=COOP, text="Two.", claim_kind="fact", round=1, entities=["Chile"]
    )

    # The entity already existed, so only the claim is a new node.
    assert [n.kind for n in delta.nodes] == ["claim"]
    assert {e.kind for e in delta.edges} == {"asserts", "about"}


def test_blank_entities_are_skipped_rather_than_making_empty_nodes() -> None:
    graph = build()

    _, delta = graph.add_claim(
        author_id=COOP, text="A point.", claim_kind="value", round=1,
        entities=["", "   ", "!!!"],
    )

    assert not [n for n in delta.nodes if n.kind == "entity"]


# --------------------------------------------------------------------------- #
# What gets refused — the lanes that keep provenance real
# --------------------------------------------------------------------------- #


def test_a_linking_pass_cannot_forge_authorship_or_citation() -> None:
    """`asserts` and `cites` have an authority behind them. Linking has not."""
    graph = build()
    first, _ = graph.add_claim(author_id=COOP, text="A.", claim_kind="fact", round=1)
    second, _ = graph.add_claim(author_id=MAXI, text="B.", claim_kind="fact", round=1)

    for forged in ("asserts", "cites", "about"):
        delta = graph.link_claims(
            source_claim=first, target_claim=second, kind=forged  # type: ignore[arg-type]
        )
        assert delta.empty, f"{forged} must not be forgeable by a linking pass"

    assert graph.link_claims(
        source_claim=first, target_claim=second, kind="contradicts"
    ).edges


def test_claims_cannot_be_linked_to_things_that_are_not_claims() -> None:
    graph = build()
    claim_id, _ = graph.add_claim(
        author_id=COOP, text="A.", claim_kind="fact", round=1, entities=["Chile"]
    )

    assert graph.link_claims(
        source_claim=claim_id, target_claim=entity_key("Chile"), kind="supports"
    ).empty
    assert graph.link_claims(
        source_claim=claim_id, target_claim=COOP, kind="supports"
    ).empty


def test_a_claim_cannot_support_itself() -> None:
    graph = build()
    claim_id, _ = graph.add_claim(author_id=COOP, text="A.", claim_kind="fact", round=1)

    assert graph.link_claims(
        source_claim=claim_id, target_claim=claim_id, kind="supports"
    ).empty


def test_evidence_for_an_unknown_claim_is_dropped_not_raised() -> None:
    """A malformed observer must never take the session down mid-round."""
    graph = build()

    assert graph.add_evidence(
        claim_id="c999", snippet="s", source_url="http://x"
    ).empty


# --------------------------------------------------------------------------- #
# The engine wiring
# --------------------------------------------------------------------------- #


def speak(text: str, *claims: Claim) -> AgentDecision:
    return AgentDecision(action="pass", thought=text, claims=list(claims))


async def test_a_claim_made_at_the_table_lands_in_the_graph_and_on_the_wire() -> None:
    engine, recorder, _ = build_engine(
        {
            COOP: [
                speak(
                    "Chile's smelters haven't recovered.",
                    Claim(
                        text="Chile's copper output is still below 2024 levels.",
                        kind="fact",
                        entities=["Chile", "copper"],
                    ),
                )
            ]
        },
        rounds=1,
    )

    await engine.run()

    view = engine.knowledge.view()
    claims = [n for n in view.nodes if n.kind == "claim"]
    assert len(claims) == 1
    assert claims[0].author_id == COOP
    assert claims[0].round == 1
    assert {n.label for n in view.nodes if n.kind == "entity"} == {"Chile", "copper"}

    frames = recorder.of_type(KnowledgeUpdateMessage)
    assert len(frames) == 1
    assert frames[0].payload.reason == "claim_made"
    assert {e.kind for e in frames[0].payload.edges} == {"asserts", "about"}


async def test_a_turn_that_argued_nothing_emits_no_knowledge_frame() -> None:
    """Most turns claim nothing, and must not cost an empty frame."""
    engine, recorder, _ = build_engine({COOP: [speak("Fine by me.")]}, rounds=1)

    await engine.run()

    assert not recorder.of_type(KnowledgeUpdateMessage)
    assert not [n for n in engine.knowledge.view().nodes if n.kind == "claim"]


async def test_what_an_agent_looked_up_is_attached_to_what_it_claimed() -> None:
    """Real provenance: the engine stamped the search, so the citation is exact."""
    decision = TurnDecision.of(
        speak(
            "Output is down and it isn't bouncing back.",
            Claim(text="Copper output fell 12% last year.", kind="fact"),
        ),
        searched=[
            SearchRecord(
                query="chile copper output 2025",
                result_snippet="Output fell 12 percent year on year.",
                source_url="https://example.org/copper",
            )
        ],
    )
    engine, _, _ = build_engine({COOP: [decision]}, rounds=1)

    await engine.run()

    view = engine.knowledge.view()
    evidence = [n for n in view.nodes if n.kind == "evidence"]
    assert len(evidence) == 1
    assert evidence[0].source_url == "https://example.org/copper"
    assert [e.kind for e in view.edges if e.kind == "cites"] == ["cites"]


async def test_the_state_snapshot_carries_the_knowledge_graph() -> None:
    engine, _, _ = build_engine(
        {COOP: [speak("A point.", Claim(text="A claim.", kind="value"))]},
        rounds=1,
    )

    await engine.run()

    snapshot = engine.snapshot()
    assert [n.kind for n in snapshot.knowledge_graph.nodes].count("claim") == 1
    # Parties are seeded, and share ids with the trust graph.
    party_ids = {n.id for n in snapshot.knowledge_graph.nodes if n.kind == "party"}
    assert party_ids >= {COOP, MAXI}
    assert party_ids >= {n.id for n in snapshot.trust_graph.nodes} & party_ids


def test_recording_the_same_edge_twice_is_not_a_duplicate() -> None:
    graph = build()
    first, _ = graph.add_claim(author_id=COOP, text="A.", claim_kind="fact", round=1)
    second, _ = graph.add_claim(author_id=MAXI, text="B.", claim_kind="fact", round=1)

    graph.link_claims(source_claim=first, target_claim=second, kind="supports")
    repeat = graph.link_claims(
        source_claim=first, target_claim=second, kind="supports"
    )

    assert repeat.empty
    assert len([e for e in graph.view().edges if e.kind == "supports"]) == 1
