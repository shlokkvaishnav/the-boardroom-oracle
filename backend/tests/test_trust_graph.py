"""Milestone 3: the trust graph in isolation — scripted offers, no LLM.

These tests double as the executable statement of the update rule, so if the
tuning constants change the intended *behaviour* is still pinned.
"""

from __future__ import annotations

import pytest

from app.engine.trust_graph import (
    DEFAULT_TUNING,
    INITIAL_WEIGHT,
    TrustGraph,
    TrustTuning,
    clamp01,
    favorability,
)

PARTIES = [("coop", "Ada"), ("max", "Rex"), ("tit", "Mira"), ("human", "You")]
POOL = 100.0


@pytest.fixture
def graph() -> TrustGraph:
    return TrustGraph(PARTIES)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("value", "expected"),
    [(-5.0, 0.0), (0.0, 0.0), (0.42, 0.42), (1.0, 1.0), (7.5, 1.0)],
)
def test_clamp01_confines_to_unit_interval(value: float, expected: float) -> None:
    assert clamp01(value) == expected


@pytest.mark.parametrize(
    ("amount", "total", "expected"),
    [
        (0.0, 100.0, 0.0),
        (25.0, 100.0, 0.25),
        (100.0, 100.0, 1.0),
        (250.0, 100.0, 1.0),  # can't be more than "all of it"
        (10.0, 0.0, 0.0),  # empty pool -> no signal, and no ZeroDivisionError
        (10.0, -1.0, 0.0),
    ],
)
def test_favorability_normalises_against_the_pool(
    amount: float, total: float, expected: float
) -> None:
    assert favorability(amount, total) == expected


def test_favorability_is_pool_size_independent() -> None:
    """The same *proportion* of a pool reads as equally generous at any scale."""
    assert favorability(20.0, 100.0) == favorability(200.0, 1000.0)


# --------------------------------------------------------------------------- #
# Construction
# --------------------------------------------------------------------------- #


def test_everyone_starts_neutral_toward_everyone(graph: TrustGraph) -> None:
    view = graph.view()

    assert {node.id for node in view.nodes} == {"coop", "max", "tit", "human"}
    # 4 parties, directed, no self-edges.
    assert len(view.edges) == 4 * 3
    assert all(edge.weight == INITIAL_WEIGHT for edge in view.edges)
    assert all(edge.last_offer_accepted is None for edge in view.edges)


def test_nodes_carry_display_labels(graph: TrustGraph) -> None:
    labels = {node.id: node.label for node in graph.view().nodes}

    assert labels["coop"] == "Ada"
    assert labels["human"] == "You"


def test_no_self_trust_edges(graph: TrustGraph) -> None:
    assert all(edge.source != edge.target for edge in graph.view().edges)


# --------------------------------------------------------------------------- #
# Rule 1 — making an offer
# --------------------------------------------------------------------------- #


def test_making_an_offer_raises_the_receivers_trust_in_the_sender(graph: TrustGraph) -> None:
    changed = graph.apply_offer_made("coop", "max", amount=50.0, pool_total=POOL)

    # weight(max -> coop): how much the receiver trusts the sender.
    assert graph.weight("max", "coop") == pytest.approx(
        INITIAL_WEIGHT + DEFAULT_TUNING.generosity_gain * 0.5
    )
    assert [(e.source, e.target) for e in changed] == [("max", "coop")]


def test_making_an_offer_leaves_the_senders_own_trust_untouched(graph: TrustGraph) -> None:
    graph.apply_offer_made("coop", "max", amount=50.0, pool_total=POOL)

    assert graph.weight("coop", "max") == INITIAL_WEIGHT


def test_a_more_generous_offer_earns_more_trust(graph: TrustGraph) -> None:
    stingy = TrustGraph(PARTIES)
    generous = TrustGraph(PARTIES)

    stingy.apply_offer_made("coop", "max", amount=5.0, pool_total=POOL)
    generous.apply_offer_made("coop", "max", amount=80.0, pool_total=POOL)

    assert generous.weight("max", "coop") > stingy.weight("max", "coop")


def test_a_zero_offer_moves_nothing(graph: TrustGraph) -> None:
    graph.apply_offer_made("coop", "max", amount=0.0, pool_total=POOL)

    assert graph.weight("max", "coop") == INITIAL_WEIGHT


# --------------------------------------------------------------------------- #
# Rule 2 — acceptance
# --------------------------------------------------------------------------- #


def test_acceptance_raises_the_senders_trust_in_the_receiver(graph: TrustGraph) -> None:
    changed = graph.apply_offer_accepted("coop", "max", amount=20.0, pool_total=POOL)

    assert graph.weight("coop", "max") == pytest.approx(
        INITIAL_WEIGHT + DEFAULT_TUNING.accept_gain * (0.5 + 0.2)
    )
    assert [(e.source, e.target) for e in changed] == [("coop", "max")]


def test_accepting_even_a_trivial_offer_still_earns_credit(graph: TrustGraph) -> None:
    """The 0.5 floor: saying yes is worth something regardless of the amount."""
    graph.apply_offer_accepted("coop", "max", amount=0.0, pool_total=POOL)

    assert graph.weight("coop", "max") > INITIAL_WEIGHT


def test_acceptance_records_the_outcome_on_the_edge(graph: TrustGraph) -> None:
    graph.apply_offer_accepted("coop", "max", amount=20.0, pool_total=POOL)

    assert graph.edge("coop", "max").last_offer_accepted is True


# --------------------------------------------------------------------------- #
# Rule 3 — rejection
# --------------------------------------------------------------------------- #


def test_rejection_lowers_the_senders_trust_in_the_receiver(graph: TrustGraph) -> None:
    changed = graph.apply_offer_rejected("coop", "max")

    assert graph.weight("coop", "max") == pytest.approx(
        INITIAL_WEIGHT - DEFAULT_TUNING.reject_penalty
    )
    assert [(e.source, e.target) for e in changed] == [("coop", "max")]


def test_rejection_records_the_outcome_on_the_edge(graph: TrustGraph) -> None:
    graph.apply_offer_rejected("coop", "max")

    assert graph.edge("coop", "max").last_offer_accepted is False


def test_rejection_penalty_is_flat_regardless_of_offer_size(graph: TrustGraph) -> None:
    """Being turned down stings the same whether 5 or 95 was on the table."""
    other = TrustGraph(PARTIES)

    graph.apply_offer_rejected("coop", "max")
    other.apply_offer_rejected("coop", "tit")

    assert graph.weight("coop", "max") == other.weight("coop", "tit")


def test_accept_and_reject_move_the_same_edge_in_opposite_directions() -> None:
    """The demo beat: one answer visibly swings the offerer's trust either way."""
    accepted = TrustGraph(PARTIES)
    rejected = TrustGraph(PARTIES)

    accepted.apply_offer_accepted("coop", "max", amount=20.0, pool_total=POOL)
    rejected.apply_offer_rejected("coop", "max")

    assert accepted.weight("coop", "max") > INITIAL_WEIGHT
    assert rejected.weight("coop", "max") < INITIAL_WEIGHT


# --------------------------------------------------------------------------- #
# Clamping and sequences
# --------------------------------------------------------------------------- #


def test_repeated_rejections_bottom_out_at_zero_not_below(graph: TrustGraph) -> None:
    for _ in range(25):
        graph.apply_offer_rejected("coop", "max")

    assert graph.weight("coop", "max") == 0.0


def test_repeated_acceptances_top_out_at_one_not_above(graph: TrustGraph) -> None:
    for _ in range(25):
        graph.apply_offer_accepted("coop", "max", amount=100.0, pool_total=POOL)

    assert graph.weight("coop", "max") == 1.0


def test_a_scripted_negotiation_produces_the_expected_standings() -> None:
    """A cooperator who gives and gets accepted ends trusted; a stonewaller doesn't."""
    graph = TrustGraph(PARTIES)

    # Ada makes a generous offer to Rex, who accepts.
    graph.apply_offer_made("coop", "max", amount=40.0, pool_total=POOL)
    graph.apply_offer_accepted("coop", "max", amount=40.0, pool_total=POOL)

    # Rex makes a stingy offer to Ada, who rejects it.
    graph.apply_offer_made("max", "coop", amount=2.0, pool_total=POOL)
    graph.apply_offer_rejected("max", "coop")

    # Ada trusts Rex more: he accepted her generous offer.
    assert graph.weight("coop", "max") == pytest.approx(0.683)

    # Rex trusts Ada *less*, even though her generosity first nudged him up:
    # the +0.06 from a 40-unit offer is outweighed by the flat -0.15 sting of
    # being rebuffed. Asymmetric trust is the interesting demo state.
    assert graph.weight("max", "coop") == pytest.approx(0.41)
    assert graph.weight("max", "coop") < INITIAL_WEIGHT < graph.weight("coop", "max")


def test_tuning_is_injectable_for_a_punchier_demo() -> None:
    aggressive = TrustGraph(PARTIES, tuning=TrustTuning(reject_penalty=0.5))

    aggressive.apply_offer_rejected("coop", "max")

    assert aggressive.weight("coop", "max") == pytest.approx(0.0)


def test_trust_toward_excludes_self_and_reports_every_other_party(graph: TrustGraph) -> None:
    graph.apply_offer_rejected("coop", "max")

    toward = graph.trust_toward("coop", graph.party_ids)

    assert set(toward) == {"max", "tit", "human"}
    assert toward["max"] < toward["tit"]


def test_weights_are_rounded_for_the_wire(graph: TrustGraph) -> None:
    """Avoid shipping float noise like 0.6500000000000001 to the frontend."""
    graph.apply_offer_made("coop", "max", amount=33.0, pool_total=POOL)

    weight = graph.edge("max", "coop").weight

    assert weight == round(weight, 4)
