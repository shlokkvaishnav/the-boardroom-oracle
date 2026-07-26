"""The wire contract with the frontend.

Field names here are load-bearing: the frontend is written against them, via
`frontend/src/lib/negotiation/adapter.ts`. `tests/test_schemas.py` pins the
exact serialized JSON so a rename can't slip through unnoticed.

Note on `from`: it's a Python keyword, so the attribute is `from_` and carries
`alias="from"`. FastAPI serializes response models with `by_alias=True` by
default, and WebSocket frames go out through `.wire()` below, so **the wire key
is always `"from"`**. `populate_by_name=True` additionally lets internal code
construct these with `from_=...` instead of `model_validate({"from": ...})`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "ContractModel",
    "utc_now_iso",
    "Pool",
    "AgentInfo",
    "GraphNode",
    "GraphEdge",
    "TrustGraphView",
    "OfferRecord",
    "OfferSchema",
    "SearchRecord",
    "AgentThought",
    "NegotiationState",
    "SessionStartRequest",
    "SessionStartResponse",
    "VoiceOfferResponse",
    "Confidence",
]


def utc_now_iso() -> str:
    """Timestamp in the format the contract specifies: an ISO 8601 UTC string."""
    return datetime.now(timezone.utc).isoformat()


class ContractModel(BaseModel):
    """Base for every model the frontend sees.

    `extra="forbid"` makes contract drift loud rather than silent: an unexpected
    key on an inbound offer is a 422, not a quietly ignored field.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    def wire(self) -> dict[str, Any]:
        """JSON-ready dict using the aliased (frontend-facing) key names."""
        return self.model_dump(by_alias=True, mode="json")


# --------------------------------------------------------------------------- #
# Building blocks
# --------------------------------------------------------------------------- #


class Pool(ContractModel):
    """The single contested resource pool."""

    resource: str
    total: float


class AgentInfo(ContractModel):
    """A seat at the table — three AI agents plus one human slot.

    `persona` is the negotiation style ("Cooperator"); it is public. The hidden
    objective deliberately has no field here: it must not reach the frontend
    before the reveal phase.
    """

    id: str
    name: str
    persona: str
    color: str
    is_human: bool


class GraphNode(ContractModel):
    id: str
    label: str


class GraphEdge(ContractModel):
    """A directed trust edge.

    `weight` is *the target's trust in the source*, in [0, 1]. Direction follows
    the offer: source made an offer to target, so target formed an opinion.
    """

    source: str
    target: str
    weight: float
    last_offer_accepted: bool | None = None


class TrustGraphView(ContractModel):
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)


class OfferSchema(ContractModel):
    """A proposed offer, before it enters the log.

    Used for both the `POST /api/session/inject-offer` body and the
    `parsed_offer` field of the voice endpoint's response.
    """

    from_: str = Field(alias="from")
    to: str
    resource: str
    amount: float


class OfferRecord(ContractModel):
    """An entry in the append-only offer log — the negotiation's audit trail.

    `accepted` is `None` while the offer is still pending.
    """

    round: int
    from_: str = Field(alias="from")
    to: str
    resource: str
    amount: float
    accepted: bool | None = None
    timestamp: str = Field(default_factory=utc_now_iso)


class SearchRecord(ContractModel):
    """Provenance for one thing an agent actually looked up.

    Server-stamped from a tool call that really ran — never model output, so it
    cannot be hallucinated. Empty on the great majority of turns.
    """

    query: str
    result_snippet: str
    source_url: str


class AgentThought(ContractModel):
    """One line of agent reasoning, surfaced in the frontend's live feed."""

    agent_id: str
    text: str
    timestamp: str = Field(default_factory=utc_now_iso)
    #: Non-empty only on turns where the agent invoked `web_search`.
    searched: list[SearchRecord] = Field(default_factory=list)


class NegotiationState(ContractModel):
    """The complete public state of a session.

    `revealed_objectives` stays `None` for the whole game and is populated only
    at the reveal.
    """

    round: int
    pool: Pool
    agents: list[AgentInfo] = Field(default_factory=list)
    trust_graph: TrustGraphView = Field(default_factory=TrustGraphView)
    offer_log: list[OfferRecord] = Field(default_factory=list)
    agent_thoughts: list[AgentThought] = Field(default_factory=list)
    revealed_objectives: dict[str, str] | None = None


# --------------------------------------------------------------------------- #
# Endpoint payloads
# --------------------------------------------------------------------------- #

Confidence = Literal["high", "low"]


class SessionStartRequest(ContractModel):
    """Optional body for `POST /api/session/start`.

    The whole body is optional, so a bare POST still starts a plain negotiation —
    the pre-existing behaviour, and what the frontend's START button does.
    """

    context_topic: str | None = Field(
        default=None,
        max_length=500,
        description=(
            "A real-world premise shared with every party, e.g. 'the 2026 copper "
            "supply squeeze'. Adds context to the same game; it does not change "
            "the rules. Also what enables the agents' web_search tool."
        ),
    )


class SessionStartResponse(ContractModel):
    session_id: str


class VoiceOfferResponse(ContractModel):
    """Returned by the voice endpoint so the frontend can preview before confirming.

    `parsed_offer` is `None` when the transcript couldn't be resolved into a
    valid offer; `confidence` tells the UI whether to require confirmation.
    """

    transcript: str
    parsed_offer: OfferSchema | None = None
    confidence: Confidence = "low"
