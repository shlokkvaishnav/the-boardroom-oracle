"""Milestone 2: pin the wire contract.

The expected JSON in this file is written out by hand rather than derived from
the models, so that renaming a field breaks a test instead of silently
rewriting the contract the frontend is built against.
"""

from __future__ import annotations

import json

import pytest
from pydantic import TypeAdapter, ValidationError

from app.models.agent_io import AgentDecision, OpponentDelta, ProposedOffer
from app.models.messages import (
    GraphUpdateMessage,
    GraphUpdatePayload,
    OfferMessage,
    ClosingMessage,
    ClosingPayload,
    RoundChangeMessage,
    RoundChangePayload,
    StateMessage,
    ThoughtMessage,
    WSMessage,
)
from app.models.schemas import (
    AgentInfo,
    AgentThought,
    GraphEdge,
    GraphNode,
    NegotiationState,
    OfferRecord,
    OfferSchema,
    Pool,
    SessionStartResponse,
    TrustGraphView,
    VoiceOfferResponse,
)


def _sample_state() -> NegotiationState:
    return NegotiationState(
        round=2,
        total_rounds=6,
        pool=Pool(resource="budget", total=100.0),
        agents=[
            AgentInfo(
                id="coop",
                name="Ada",
                persona="Cooperator",
                color="#3BA55D",
                is_human=False,
            ),
            AgentInfo(
                id="human",
                name="You",
                persona="Human",
                color="#F2F3F5",
                is_human=True,
            ),
        ],
        trust_graph=TrustGraphView(
            nodes=[GraphNode(id="coop", label="Ada"), GraphNode(id="human", label="You")],
            edges=[
                GraphEdge(
                    source="coop",
                    target="human",
                    weight=0.62,
                    last_offer_accepted=True,
                )
            ],
        ),
        offer_log=[
            OfferRecord(
                round=1,
                from_="coop",
                to="human",
                resource="budget",
                amount=25.0,
                accepted=True,
                timestamp="2026-07-26T12:00:00+00:00",
            )
        ],
        agent_thoughts=[
            AgentThought(
                agent_id="coop",
                text="Opening generously to establish trust.",
                round=1,
                stance=0.4,
                timestamp="2026-07-26T12:00:00+00:00",
            )
        ],
    )


# --------------------------------------------------------------------------- #
# NegotiationState — the shape the frontend polls and receives on connect
# --------------------------------------------------------------------------- #


def test_negotiation_state_serializes_to_exact_expected_json() -> None:
    assert _sample_state().wire() == {
        "round": 2,
        # Carried on the snapshot as well as on `round_change`, so a client that
        # connects mid-game knows the length without hardcoding it.
        "total_rounds": 6,
        "pool": {"resource": "budget", "total": 100.0},
        "agents": [
            {
                "id": "coop",
                "name": "Ada",
                "persona": "Cooperator",
                "color": "#3BA55D",
                "is_human": False,
            },
            {
                "id": "human",
                "name": "You",
                "persona": "Human",
                "color": "#F2F3F5",
                "is_human": True,
            },
        ],
        "trust_graph": {
            "nodes": [
                {"id": "coop", "label": "Ada"},
                {"id": "human", "label": "You"},
            ],
            "edges": [
                {
                    "source": "coop",
                    "target": "human",
                    "weight": 0.62,
                    "last_offer_accepted": True,
                }
            ],
        },
        # Empty until parties start making claims, which needs a context topic.
        "knowledge_graph": {"nodes": [], "edges": []},
        "offer_log": [
            {
                "round": 1,
                "from": "coop",
                "to": "human",
                "resource": "budget",
                "amount": 25.0,
                "accepted": True,
                "timestamp": "2026-07-26T12:00:00+00:00",
                # Stable handle so a client can answer this specific offer.
                "offer_id": "",
            }
        ],
        "agent_thoughts": [
            {
                "agent_id": "coop",
                "text": "Opening generously to establish trust.",
                # Round places the line in the transcript; stance is where the
                # speaker stood when they said it, null with no topic set.
                "round": 1,
                "stance": 0.4,
                "timestamp": "2026-07-26T12:00:00+00:00",
                # Present on every thought, empty on the great majority of
                # turns — only a turn that actually ran `web_search` fills it.
                "searched": [],
            }
        ],
        # The stake, visible while it is still in play.
        "holdings": {},
        "closing_positions": None,
    }


def test_state_has_exactly_the_contracted_top_level_keys() -> None:
    assert set(_sample_state().wire()) == {
        "round",
        "total_rounds",
        "pool",
        "agents",
        "trust_graph",
        "knowledge_graph",
        "offer_log",
        "agent_thoughts",
        "holdings",
        "closing_positions",
    }


def test_closing_positions_is_null_before_the_reveal() -> None:
    assert _sample_state().wire()["closing_positions"] is None


def test_agent_info_never_carries_a_hidden_objective_field() -> None:
    """Hidden objectives must be structurally impossible to leak pre-reveal."""
    assert "objective" not in AgentInfo.model_fields
    with pytest.raises(ValidationError):
        AgentInfo(
            id="x",
            name="X",
            persona="Maximizer",
            color="#fff",
            is_human=False,
            objective="hold >60% of the pool",  # type: ignore[call-arg]
        )


# --------------------------------------------------------------------------- #
# The `from` alias, in both directions
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("model", [OfferRecord, OfferSchema])
def test_sender_field_serializes_as_from_not_from_underscore(model: type) -> None:
    kwargs = {"from_": "a", "to": "b", "resource": "budget", "amount": 10.0}
    if model is OfferRecord:
        kwargs["round"] = 1

    payload = model(**kwargs).wire()

    assert payload["from"] == "a"
    assert "from_" not in payload


def test_offer_schema_parses_the_documented_request_body() -> None:
    """Exactly the body shape documented for POST /api/session/inject-offer."""
    offer = OfferSchema.model_validate(
        json.loads('{"from": "human", "to": "max", "resource": "budget", "amount": 12.5}')
    )

    assert (offer.from_, offer.to, offer.resource, offer.amount) == (
        "human",
        "max",
        "budget",
        12.5,
    )


def test_offer_schema_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        OfferSchema.model_validate(
            {"from": "a", "to": "b", "resource": "budget", "amount": 1.0, "sneaky": True}
        )


def test_offer_record_defaults_pending_and_timestamps_itself() -> None:
    record = OfferRecord(round=1, from_="a", to="b", resource="budget", amount=5.0)

    assert record.accepted is None
    assert record.timestamp.endswith("+00:00")


# --------------------------------------------------------------------------- #
# Endpoint payloads
# --------------------------------------------------------------------------- #


def test_session_start_response_shape() -> None:
    assert SessionStartResponse(session_id="abc123").wire() == {"session_id": "abc123"}


def test_voice_offer_response_shape_when_parsed() -> None:
    response = VoiceOfferResponse(
        transcript="give the maximizer twelve budget",
        parsed_offer=OfferSchema(from_="human", to="max", resource="budget", amount=12.0),
        confidence="high",
    )

    assert response.wire() == {
        "transcript": "give the maximizer twelve budget",
        "parsed_offer": {"from": "human", "to": "max", "resource": "budget", "amount": 12.0},
        "confidence": "high",
    }


def test_voice_offer_response_shape_when_unparseable() -> None:
    response = VoiceOfferResponse(transcript="uhh what were we doing")

    assert response.wire() == {
        "transcript": "uhh what were we doing",
        "parsed_offer": None,
        "confidence": "low",
    }


def test_voice_offer_confidence_is_constrained_to_high_or_low() -> None:
    with pytest.raises(ValidationError):
        VoiceOfferResponse(transcript="x", confidence="medium")  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# WebSocket frames
# --------------------------------------------------------------------------- #


def test_every_ws_frame_is_type_plus_payload() -> None:
    state = _sample_state()
    frames = [
        StateMessage(payload=state),
        OfferMessage(payload=state.offer_log[0]),
        GraphUpdateMessage(
            payload=GraphUpdatePayload(edges=state.trust_graph.edges, reason="offer_accepted")
        ),
        ThoughtMessage(payload=state.agent_thoughts[0]),
        RoundChangeMessage(payload=RoundChangePayload(round=3, total_rounds=6)),
        ClosingMessage(
            payload=ClosingPayload(
                positions={"coop": "Rushing this through sets a precedent we'll regret."},
                final_state=state,
            )
        ),
    ]

    assert [f.wire()["type"] for f in frames] == [
        "state",
        "offer",
        "graph_update",
        "thought",
        "round_change",
        "closing",
    ]
    for frame in frames:
        assert set(frame.wire()) == {"type", "payload"}


def test_ws_union_discriminates_on_type() -> None:
    adapter = TypeAdapter(WSMessage)

    decoded = adapter.validate_python(
        {"type": "round_change", "payload": {"round": 4, "total_rounds": 6}}
    )

    assert isinstance(decoded, RoundChangeMessage)
    assert decoded.payload.round == 4


def test_ws_offer_frame_keeps_the_from_alias_through_the_envelope() -> None:
    frame = OfferMessage(
        payload=OfferRecord(round=1, from_="human", to="coop", resource="budget", amount=8.0)
    )

    assert json.loads(frame.model_dump_json(by_alias=True))["payload"]["from"] == "human"


# --------------------------------------------------------------------------- #
# Agent decision contract (internal, LLM-facing)
# --------------------------------------------------------------------------- #


def test_offer_action_requires_an_offer_object() -> None:
    with pytest.raises(ValidationError, match="requires a non-null `offer`"):
        AgentDecision(action="offer", thought="I'll propose something")


def test_accept_action_requires_a_target_offer_id() -> None:
    with pytest.raises(ValidationError, match="requires `target_offer_id`"):
        AgentDecision(action="accept", thought="Deal.")


def test_pass_action_must_carry_no_payload() -> None:
    with pytest.raises(ValidationError, match="must set neither"):
        AgentDecision(
            action="pass",
            thought="Waiting.",
            offer=ProposedOffer(to="max", resource="budget", amount=5.0),
        )


def test_valid_decisions_round_trip() -> None:
    offering = AgentDecision(
        action="offer",
        offer=ProposedOffer(to="max", resource="budget", amount=20.0),
        thought="Testing their appetite with a modest opener.",
        opponent_updates=[OpponentDelta(agent_id="max", trust_delta=-0.1, note="stonewalled me")],
    )
    accepting = AgentDecision(action="accept", target_offer_id="o3", thought="Good enough.")

    assert offering.offer is not None and offering.offer.amount == 20.0
    assert accepting.target_offer_id == "o3"


def test_safe_default_is_always_a_valid_pass() -> None:
    fallback = AgentDecision.safe_default("invalid JSON twice")

    assert fallback.action == "pass"
    assert fallback.offer is None and fallback.target_offer_id is None
    assert "invalid JSON twice" in fallback.thought
