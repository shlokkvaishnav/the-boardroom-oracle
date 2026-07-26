"""The trust graph and its update rule.

This module is deliberately boring. The edge-weight rule gets explained out
loud during the live demo, so every constant is visible in one dataclass and
every event has its own named function with a one-sentence justification. No
hidden state, no accumulated history — the weight is a pure function of its
previous value and the event that just happened.

EDGE DIRECTION
    An edge points **from the truster to the trusted**:
    `weight(A -> B)` is *how much A trusts B*, in [0, 1], starting at 0.5.

THE RULE
    favorability = amount / pool_total        # how generous an offer is, 0..1

    A offers to B      ->  B saw A be generous, so B trusts A more:
                           weight(B -> A) += GENEROSITY_GAIN * favorability

    B accepts A's offer ->  B proved a willing partner, so A trusts B more:
                           weight(A -> B) += ACCEPT_GAIN * (0.5 + favorability)

    B rejects A's offer ->  A was rebuffed, so A trusts B less:
                           weight(A -> B) -= REJECT_PENALTY

    Every result is clamped back into [0, 1].

Accept and reject move the *same* edge in opposite directions, which is the
demo beat worth pointing at: one agent's answer visibly swings the other's
trust. Making an offer moves the other edge, so generosity is rewarded even
before anyone replies.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

import networkx as nx

from app.models.schemas import GraphEdge, GraphNode, TrustGraphView

__all__ = [
    "TrustTuning",
    "DEFAULT_TUNING",
    "INITIAL_WEIGHT",
    "clamp01",
    "favorability",
    "bundle_favorability",
    "TrustGraph",
]

#: Every relationship starts neutral — neither trusting nor suspicious.
INITIAL_WEIGHT = 0.5


@dataclass(frozen=True)
class TrustTuning:
    """The four numbers that govern trust. Tune these to change the demo's feel.

    They are intentionally small: a single event should nudge, not swing. With
    the defaults, a maximally generous offer (the whole pool) moves the
    receiver's trust by 0.15, and a rejection costs a flat 0.15 — so roughly
    three rejections take a neutral relationship to near-zero.
    """

    generosity_gain: float = 0.15
    accept_gain: float = 0.20
    reject_penalty: float = 0.15


DEFAULT_TUNING = TrustTuning()


def clamp01(value: float) -> float:
    """Confine a weight to [0, 1]."""
    return max(0.0, min(1.0, value))


def bundle_favorability(
    lines: Mapping[str, float], totals: Mapping[str, float]
) -> float:
    """How generous a multi-issue offer is, in [0, 1].

    THE RULE
        Normalise each issue against its own total, then take the mean across
        *every* issue on the table — including the ones this offer does not
        touch, which contribute zero.

            favorability = (1/n) * SUM over issues of (amount_i / total_i)

    Two properties make this the right rule, and both are load-bearing because
    this number feeds the trust graph.

    **It is unit-safe.** Issues are measured in different things — dollars,
    weeks, engineers — so raw amounts cannot be added. Normalising first is the
    only way to compare "30 of the budget" with "2 of the 6 weeks".

    **It answers "how much of everything did you hand over?"** Averaging over
    all issues rather than only the touched ones is the part worth stating out
    loud. Give away one issue entirely on a four-issue table and this reads
    0.25, not 1.0 — because you gave a quarter of what was on the table, and a
    rule that scored it 1.0 would let an agent buy maximum trust with the one
    issue it happened not to care about.

    Single-issue play is the one-element case and reduces exactly to
    `amount / total`, which is what the rule has always been. There is no
    separate path for it.
    """
    live = {issue: total for issue, total in totals.items() if total > 0}
    if not live:
        return 0.0
    share = sum(clamp01(lines.get(issue, 0.0) / total) for issue, total in live.items())
    return clamp01(share / len(live))


def favorability(amount: float, pool_total: float) -> float:
    """The single-issue case of `bundle_favorability`.

    Kept as its own name because the one-pool code path still calls it, and
    because "fraction of the pool" is how the rule gets explained out loud.
    """
    return bundle_favorability({"pool": amount}, {"pool": pool_total})


class TrustGraph:
    """A directed trust network over the parties at the table.

    Thin wrapper over `networkx.DiGraph`: networkx owns storage and traversal,
    this class owns the update rule.
    """

    def __init__(
        self,
        parties: Sequence[tuple[str, str]],
        tuning: TrustTuning = DEFAULT_TUNING,
    ) -> None:
        """`parties` is a sequence of `(id, label)` pairs."""
        self.tuning = tuning
        self._graph = nx.DiGraph()

        for party_id, label in parties:
            self._graph.add_node(party_id, label=label)

        # Everyone starts neutral toward everyone else, in both directions.
        for truster, _ in parties:
            for trusted, _ in parties:
                if truster != trusted:
                    self._graph.add_edge(
                        truster,
                        trusted,
                        weight=INITIAL_WEIGHT,
                        last_offer_accepted=None,
                    )

    # -- reads ------------------------------------------------------------- #

    @property
    def party_ids(self) -> list[str]:
        return list(self._graph.nodes)

    def weight(self, truster: str, trusted: str) -> float:
        """How much `truster` trusts `trusted`."""
        return float(self._graph.edges[truster, trusted]["weight"])

    def edge(self, truster: str, trusted: str) -> GraphEdge:
        data = self._graph.edges[truster, trusted]
        return GraphEdge(
            source=truster,
            target=trusted,
            weight=round(float(data["weight"]), 4),
            last_offer_accepted=data["last_offer_accepted"],
        )

    def view(self) -> TrustGraphView:
        """The whole graph in the frontend's shape."""
        return TrustGraphView(
            nodes=[
                GraphNode(id=node_id, label=data["label"])
                for node_id, data in self._graph.nodes(data=True)
            ],
            edges=[self.edge(source, target) for source, target in self._graph.edges],
        )

    # -- the update rule --------------------------------------------------- #

    def _nudge(self, truster: str, trusted: str, delta: float) -> GraphEdge:
        """Apply one delta to one edge and clamp. The only place weight changes."""
        data = self._graph.edges[truster, trusted]
        data["weight"] = clamp01(float(data["weight"]) + delta)
        return self.edge(truster, trusted)

    def apply_offer_made(
        self,
        sender: str,
        receiver: str,
        amount: float,
        pool_total: float,
    ) -> list[GraphEdge]:
        """The receiver saw the sender be generous, so trusts them a little more.

        Rewards the *gesture*, before any reply — which is what lets a
        cooperative agent build trust even when its offers get turned down.
        """
        delta = self.tuning.generosity_gain * favorability(amount, pool_total)
        return [self._nudge(receiver, sender, delta)]

    def apply_offer_accepted(
        self,
        sender: str,
        receiver: str,
        amount: float,
        pool_total: float,
    ) -> list[GraphEdge]:
        """The receiver accepted, proving a willing partner — the sender trusts them more.

        The `0.5 +` floor means accepting *anything* earns credit, with a
        generous deal earning up to half again as much.
        """
        delta = self.tuning.accept_gain * (0.5 + favorability(amount, pool_total))
        self._graph.edges[sender, receiver]["last_offer_accepted"] = True
        return [self._nudge(sender, receiver, delta)]

    def apply_offer_rejected(self, sender: str, receiver: str) -> list[GraphEdge]:
        """The sender was rebuffed, so trusts the receiver less.

        A flat penalty, independent of the offer's size: the sting is in being
        turned down, not in how much was on the table.
        """
        self._graph.edges[sender, receiver]["last_offer_accepted"] = False
        return [self._nudge(sender, receiver, -self.tuning.reject_penalty)]

    # -- convenience -------------------------------------------------------- #

    def trust_toward(self, truster: str, others: Iterable[str]) -> dict[str, float]:
        """`truster`'s trust in each of `others` — fed into the opponent model."""
        return {
            other: self.weight(truster, other) for other in others if other != truster
        }
