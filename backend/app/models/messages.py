"""WebSocket frames pushed to the frontend on `/ws/negotiation`.

Every frame is `{"type": ..., "payload": {...}}`, discriminated on `type`.

CONTRACT NOTE â€” `state` is an addition to the type list in the original spec.
The spec required "on connect, send the current full NegotiationState" but
listed the union as "e.g. offer | graph_update | thought | round_change |
reveal", with no variant able to carry a full state snapshot. `state` fills
that gap and is also re-sent after a reset.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import Field

from app.models.schemas import (
    AgentThought,
    ContractModel,
    GraphEdge,
    NegotiationState,
    OfferRecord,
)

__all__ = [
    "StateMessage",
    "OfferMessage",
    "GraphUpdateMessage",
    "ThoughtMessage",
    "RoundChangeMessage",
    "ClosingMessage",
    "GraphUpdatePayload",
    "RoundChangePayload",
    "ClosingPayload",
    "WSMessage",
]


# --------------------------------------------------------------------------- #
# Payloads that aren't already a top-level schema
# --------------------------------------------------------------------------- #


class GraphUpdatePayload(ContractModel):
    """The edges whose weight changed as a result of one action.

    A single action moves exactly one edge, but this is a list so that session
    start and reset can push the whole graph through the same frame type.
    `reason` names the rule that fired, so the UI can label the change.
    """

    edges: list[GraphEdge] = Field(default_factory=list)
    reason: str


class RoundChangePayload(ContractModel):
    round: int
    total_rounds: int


class ClosingPayload(ContractModel):
    """The end of the discussion: where each party finished standing.

    `positions` is each party's last statement on the topic — their closing
    argument, taken from what they actually said rather than generated
    separately, so it costs no extra provider call and cannot contradict the
    transcript above it.

    Final holdings are read from `final_state.holdings`. They were briefly a
    sibling field here too, which meant one number had two sources that could
    drift apart; the snapshot is the authoritative one.
    """

    positions: dict[str, str]
    final_state: NegotiationState


# --------------------------------------------------------------------------- #
# Frames
# --------------------------------------------------------------------------- #


class StateMessage(ContractModel):
    type: Literal["state"] = "state"
    payload: NegotiationState


class OfferMessage(ContractModel):
    type: Literal["offer"] = "offer"
    payload: OfferRecord


class GraphUpdateMessage(ContractModel):
    type: Literal["graph_update"] = "graph_update"
    payload: GraphUpdatePayload


class ThoughtMessage(ContractModel):
    type: Literal["thought"] = "thought"
    payload: AgentThought


class RoundChangeMessage(ContractModel):
    type: Literal["round_change"] = "round_change"
    payload: RoundChangePayload


class ClosingMessage(ContractModel):
    type: Literal["closing"] = "closing"
    payload: ClosingPayload


WSMessage = Annotated[
    Union[
        StateMessage,
        OfferMessage,
        GraphUpdateMessage,
        ThoughtMessage,
        RoundChangeMessage,
        ClosingMessage,
    ],
    Field(discriminator="type"),
]
