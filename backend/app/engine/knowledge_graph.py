"""What was argued, as a graph.

The trust graph next door answers *who trusts whom*. This one answers *what was
actually said, and what it rests on* — the substance rather than the social
dynamics. Before it existed the argument survived only as a scrolling text feed
that vanished when the tab closed.

Like `trust_graph.py`, this is deliberately boring: networkx owns storage, this
class owns the rules, and every event has its own named method. No inference
happens here. Nodes and edges are recorded exactly as reported, by whoever is
entitled to report them.

WHO MAY SAY WHAT
    An agent reports its own claims (`asserts`) and what they are about
    (`about`), because it is the only party that knows what it meant. The
    engine stamps evidence (`cites`) from search records that really ran. A
    scribe reading the whole round adds `supports` and `contradicts`, because
    noticing that one claim rebuts another said two turns ago by someone else
    is a cross-transcript judgement no single speaker can make. A fact-checker
    stamps verdicts.

    Keeping those lanes separate is what stops the graph from becoming a place
    where a model asserts things about other models' arguments unchallenged.

NODE IDENTITY
    Parties reuse their negotiation ids, so `cooperator` is the same node here
    as in the trust graph. Claims are sequential (`c1`, `c2`). Entities and
    evidence are keyed by a normalised natural key, so the same smelter
    mentioned in four rounds is one node with four edges into it — which is the
    entire point of drawing this as a graph rather than a list.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

import networkx as nx

from app.models.schemas import (
    KnowledgeEdge,
    KnowledgeEdgeKind,
    KnowledgeGraphView,
    KnowledgeNode,
    Verdict,
)

logger = logging.getLogger("boardroom.knowledge")

__all__ = ["KnowledgeDelta", "KnowledgeGraph", "entity_key"]

#: Claim text longer than this is truncated for the node label. The full text
#: stays on the claim node; this only keeps a graph node from rendering as a
#: paragraph.
MAX_LABEL = 120


def entity_key(name: str) -> str:
    """Normalise an entity name into a merge key.

    Case and surrounding punctuation are noise — "Chile", "chile," and "Chile"
    are one thing, and treating them as three would defeat the only reason to
    have entity nodes at all. Deliberately not stemming or fuzzy-matching:
    silently merging two genuinely different entities is a far worse failure
    than leaving two spellings apart, and this runs with no human to correct it.
    """
    cleaned = re.sub(r"[^\w\s-]", "", name).strip().lower()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return f"e:{cleaned}"


@dataclass
class KnowledgeDelta:
    """What one event added. Emitted as an incremental frame.

    Both lists are additive — this graph never removes or rewrites, so a client
    can merge a delta by upsert and never needs to reconcile a deletion. The
    one thing that mutates in place is a claim's `verdict`, which arrives as the
    same node id with a filled-in field.
    """

    nodes: list[KnowledgeNode] = field(default_factory=list)
    edges: list[KnowledgeEdge] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.nodes and not self.edges


class KnowledgeGraph:
    """The argument so far.

    Thin wrapper over `networkx.DiGraph`: networkx owns storage and traversal,
    this class owns what may be recorded and by whom.
    """

    def __init__(self, parties: Sequence[tuple[str, str]] = ()) -> None:
        """`parties` is a sequence of `(id, label)` pairs, as in the trust graph."""
        self._graph = nx.DiGraph()
        self._claim_seq = 0
        for party_id, label in parties:
            self._graph.add_node(party_id, kind="party", label=label)

    # -- reads -------------------------------------------------------------- #

    def __contains__(self, node_id: str) -> bool:
        return node_id in self._graph

    @property
    def claim_ids(self) -> list[str]:
        return [n for n, d in self._graph.nodes(data=True) if d["kind"] == "claim"]

    def node(self, node_id: str) -> KnowledgeNode:
        data = self._graph.nodes[node_id]
        return KnowledgeNode(
            id=node_id,
            kind=data["kind"],
            label=data["label"],
            round=data.get("round"),
            author_id=data.get("author_id"),
            claim_kind=data.get("claim_kind"),
            verdict=data.get("verdict"),
            source_url=data.get("source_url"),
        )

    def view(self) -> KnowledgeGraphView:
        """The whole graph in the frontend's shape."""
        return KnowledgeGraphView(
            nodes=[self.node(node_id) for node_id in self._graph.nodes],
            edges=[
                KnowledgeEdge(source=source, target=target, kind=data["kind"])
                for source, target, data in self._graph.edges(data=True)
            ],
        )

    def claims_by(self, author_id: str) -> list[KnowledgeNode]:
        """Everything one party has asserted, oldest first."""
        return [
            self.node(node_id)
            for node_id in self._graph.nodes
            if self._graph.nodes[node_id].get("author_id") == author_id
        ]

    # -- internals ---------------------------------------------------------- #

    def _upsert(self, node_id: str, **attrs: object) -> tuple[KnowledgeNode, bool]:
        """Add a node, or leave an existing one alone. Returns (node, is_new)."""
        if node_id in self._graph:
            return self.node(node_id), False
        self._graph.add_node(node_id, **attrs)
        return self.node(node_id), True

    def _connect(
        self, source: str, target: str, kind: KnowledgeEdgeKind
    ) -> KnowledgeEdge | None:
        """Add a typed edge, unless that exact edge is already there."""
        if self._graph.has_edge(source, target):
            if self._graph.edges[source, target]["kind"] == kind:
                return None
        self._graph.add_edge(source, target, kind=kind)
        return KnowledgeEdge(source=source, target=target, kind=kind)

    # -- the update rules --------------------------------------------------- #

    def add_claim(
        self,
        *,
        author_id: str,
        text: str,
        claim_kind: str,
        round: int,
        entities: Iterable[str] = (),
    ) -> tuple[str, KnowledgeDelta]:
        """Record one assertion, its author, and what it is about.

        Returns the new claim's id alongside the delta, because callers
        immediately need it — evidence and verdicts both attach to a claim.
        """
        self._claim_seq += 1
        claim_id = f"c{self._claim_seq}"
        label = text if len(text) <= MAX_LABEL else text[: MAX_LABEL - 1] + "…"

        node, _ = self._upsert(
            claim_id,
            kind="claim",
            label=label,
            round=round,
            author_id=author_id,
            claim_kind=claim_kind,
            verdict="unchecked",
        )
        delta = KnowledgeDelta(nodes=[node])

        # The author must exist as a node for the edge to mean anything. It
        # normally does — parties are seeded at construction — but a claim from
        # an unseeded id should still land rather than raise mid-round.
        author, is_new = self._upsert(author_id, kind="party", label=author_id)
        if is_new:
            delta.nodes.append(author)
        edge = self._connect(author_id, claim_id, "asserts")
        if edge:
            delta.edges.append(edge)

        for name in entities:
            cleaned = name.strip()
            if not cleaned:
                continue
            entity_id = entity_key(cleaned)
            if entity_id == "e:":
                continue
            entity, is_new = self._upsert(entity_id, kind="entity", label=cleaned)
            if is_new:
                delta.nodes.append(entity)
            edge = self._connect(claim_id, entity_id, "about")
            if edge:
                delta.edges.append(edge)

        return claim_id, delta

    def add_evidence(
        self, *, claim_id: str, snippet: str, source_url: str
    ) -> KnowledgeDelta:
        """Attach a real search result to a claim.

        Evidence nodes are keyed by URL, so one source cited by three different
        claims is one node with three edges — which is how you see at a glance
        that a whole line of argument rests on a single article.
        """
        if claim_id not in self._graph:
            logger.warning("evidence for unknown claim %r, dropping", claim_id)
            return KnowledgeDelta()

        evidence_id = f"v:{source_url}"
        label = snippet if len(snippet) <= MAX_LABEL else snippet[: MAX_LABEL - 1] + "…"
        node, is_new = self._upsert(
            evidence_id, kind="evidence", label=label, source_url=source_url
        )

        delta = KnowledgeDelta(nodes=[node] if is_new else [])
        edge = self._connect(claim_id, evidence_id, "cites")
        if edge:
            delta.edges.append(edge)
        return delta

    def link_claims(
        self, *, source_claim: str, target_claim: str, kind: KnowledgeEdgeKind
    ) -> KnowledgeDelta:
        """Record that one claim supports or contradicts another.

        Only these two kinds are accepted: `asserts`, `about` and `cites` have
        an authority behind them — the speaker, or a search that really ran —
        and letting a linking pass forge one would put invented provenance in a
        graph whose whole value is that its provenance is real.
        """
        if kind not in ("supports", "contradicts"):
            logger.warning("refusing to link claims with edge kind %r", kind)
            return KnowledgeDelta()
        if source_claim == target_claim:
            return KnowledgeDelta()
        for node_id in (source_claim, target_claim):
            if self._graph.nodes.get(node_id, {}).get("kind") != "claim":
                logger.warning("link references %r, which is not a claim", node_id)
                return KnowledgeDelta()

        edge = self._connect(source_claim, target_claim, kind)
        return KnowledgeDelta(edges=[edge] if edge else [])

    def set_verdict(self, *, claim_id: str, verdict: Verdict) -> KnowledgeDelta:
        """Stamp a fact-check outcome onto a claim.

        The one mutation in an otherwise append-only graph, so it is emitted as
        the same node id carrying a changed field — a client merging by upsert
        gets it right without special handling.
        """
        data = self._graph.nodes.get(claim_id)
        if data is None or data["kind"] != "claim":
            logger.warning("verdict for unknown claim %r, dropping", claim_id)
            return KnowledgeDelta()
        data["verdict"] = verdict
        return KnowledgeDelta(nodes=[self.node(claim_id)])
