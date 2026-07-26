"""WebSocket frames pushed to the frontend on `/ws/negotiation`.

Every frame is `{"type": ..., "payload": {...}}`, discriminated on `type`.

CONTRACT NOTE — `state` is an addition to the type list in the original spec.
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
    "RevealMessage",
    "GraphUpdatePayload",
    "RoundChangePayload",
    "RevealPayload",
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


class RevealPayload(ContractModel):
    """The endgame frame: hidden objectives become public and are scored."""

    revealed_objectives: dict[str, str]
    # Fraction of each agent's true objective achieved, in [0, 1].
    scores: dict[str, float]
    holdings: dict[str, float]
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


class RevealMessage(ContractModel):
    type: Literal["reveal"] = "reveal"
    payload: RevealPayload


WSMessage = Annotated[
    Union[
        StateMessage,
        OfferMessage,
        GraphUpdateMessage,
        ThoughtMessage,
        RoundChangeMessage,
        RevealMessage,
    ],
    Field(discriminator="type"),
]
