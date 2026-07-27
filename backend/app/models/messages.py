"""WebSocket frames pushed to the frontend on `/ws/negotiation`.

Every frame is `{"type": ..., "payload": {...}}`, discriminated on `type`.

The union is `state | round_change | thought | whisper | knowledge_update |
offer | graph_update | closing`. `state` is the full snapshot, sent once on
connect and again after a reset; everything else is an incremental delta the
client merges into it. `closing` carries a final snapshot of its own, so a
client that joins late still ends up with a complete picture.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import Field

from app.models.schemas import (
    AgentThought,
    ContractModel,
    GraphEdge,
    KnowledgeEdge,
    KnowledgeNode,
    NegotiationState,
    OfferRecord,
    WhisperRecord,
)

__all__ = [
    "StateMessage",
    "OfferMessage",
    "GraphUpdateMessage",
    "ThoughtMessage",
    "WhisperMessage",
    "KnowledgeUpdateMessage",
    "RoundChangeMessage",
    "ClosingMessage",
    "GraphUpdatePayload",
    "KnowledgeUpdatePayload",
    "RoundChangePayload",
    "ClosingPayload",
    "WSMessage",
]


# --------------------------------------------------------------------------- #
# Payloads that aren't already a top-level schema
# --------------------------------------------------------------------------- #


class GraphUpdatePayload(ContractModel):
    """The edges whose weight changed as a result of one action.

    In practice a single action moves exactly one edge; the list leaves room for
    a rule that moves several. Nodes never ride this frame — a client learns the
    roster from `state` and only ever merges edge weights here. `reason` names
    the rule that fired, so the UI can label the change.
    """

    edges: list[GraphEdge] = Field(default_factory=list)
    reason: str


class KnowledgeUpdatePayload(ContractModel):
    """What one event added to the knowledge graph.

    Purely additive, so a client merges by upserting on node id and edge
    (source, target) — nothing is ever removed, and nothing is ever rewritten.
    `reason` names what produced this, so the UI can distinguish a claim the
    speaker made from a link a scribe inferred.
    """

    nodes: list[KnowledgeNode] = Field(default_factory=list)
    edges: list[KnowledgeEdge] = Field(default_factory=list)
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

    `agreed` and `unresolved` come from the rapporteur. Both stay empty when it
    could not run, which is why `synthesised` exists: an empty `agreed` from a
    real report means the room converged on nothing, and that is a genuine
    outcome the UI should show rather than hide.
    """

    positions: dict[str, str]
    final_state: NegotiationState
    agreed: list[str] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)
    #: False when the closing fell back to each party's last remark.
    synthesised: bool = False


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


class WhisperMessage(ContractModel):
    """A private aside, pushed to viewers but to no other agent."""

    type: Literal["whisper"] = "whisper"
    payload: WhisperRecord


class KnowledgeUpdateMessage(ContractModel):
    type: Literal["knowledge_update"] = "knowledge_update"
    payload: KnowledgeUpdatePayload


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
        WhisperMessage,
        KnowledgeUpdateMessage,
        RoundChangeMessage,
        ClosingMessage,
    ],
    Field(discriminator="type"),
]
