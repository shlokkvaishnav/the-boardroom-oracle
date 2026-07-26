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
    "Issue",
    "OfferLine",
    "AgentInfo",
    "GraphNode",
    "GraphEdge",
    "TrustGraphView",
    "KnowledgeNodeKind",
    "KnowledgeEdgeKind",
    "Verdict",
    "KnowledgeNode",
    "KnowledgeEdge",
    "KnowledgeGraphView",
    "OfferRecord",
    "OfferSchema",
    "SearchRecord",
    "AgentThought",
    "WhisperRecord",
    "NegotiationState",
    "OfferResponseRequest",
    "SessionStartRequest",
    "SessionStartResponse",
    "TranscriptResponse",
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
    """The single contested resource pool.

    Being replaced by `Issue`: one pool makes the table zero-sum by
    construction, so the only move available to anyone is "give me more".
    Kept until the migration to `issues` is complete.
    """

    resource: str
    total: float


class Issue(ContractModel):
    """One thing on the table, with its own units and its own quantity.

    Several issues is what makes the negotiation *integrative* rather than
    merely distributive. With one pool, every gain is someone's loss and the
    only available argument is about the split. With budget, timeline and
    headcount on the table — each valued differently by each party — there are
    trades that leave both sides better off, and finding one is the actual
    skill the demo is meant to show.

    Single-issue play is exactly the one-element case, so nothing needs a
    special path.
    """

    id: str
    label: str
    total: float
    #: Optional unit for display: "weeks", "engineers". Purely cosmetic.
    unit: str | None = None


class OfferLine(ContractModel):
    """One issue's worth of a proposed transfer.

    An offer is a *bundle* of these, which is the whole point: "you take the
    budget, I take the deadline" is one offer with two lines, and cannot be
    expressed at all as a single amount.
    """

    issue: str
    amount: float


class AgentInfo(ContractModel):
    """A seat at the table — three AI agents plus one human slot.

    `persona` is the negotiation style ("Cooperator"); it is public. The hidden
    session carries no hidden goals, so there is nothing else to withhold.
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
    #: Stable handle so a client can answer a specific pending offer. Empty for
    #: records created before the offer existed in the pending queue.
    offer_id: str = ""


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
    #: Which round this was said in. A transcript entry with no round is hard
    #: to place, and it is what lets stance be plotted against time.
    round: int = 0
    #: Where the speaker stood on the topic when they said it, -1 to 1.
    #:
    #: Self-reported, so it costs nothing — it rides the response the agent was
    #: already sending. `None` whenever there is no topic to have a position on,
    #: which is every session started without one.
    stance: float | None = None
    timestamp: str = Field(default_factory=utc_now_iso)
    #: Non-empty only on turns where the agent invoked `web_search`.
    searched: list[SearchRecord] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Knowledge graph — what was argued, as opposed to who trusts whom
# --------------------------------------------------------------------------- #

#: What a node in the knowledge graph represents.
#:
#: `party` deliberately reuses the party ids from `AgentInfo`, so the two graphs
#: share node identity: the Ada in the trust graph and the Ada who asserted a
#: claim are the same node, and a client can cross-reference them without a
#: lookup table.
KnowledgeNodeKind = Literal["party", "claim", "entity", "evidence"]

#: How two nodes relate.
#:
#: `asserts` and `about` are reported by the speaker itself. `supports` and
#: `contradicts` are cross-transcript judgements, so they can only be produced
#: by something that reads the whole round — see the scribe.
KnowledgeEdgeKind = Literal["asserts", "about", "cites", "supports", "contradicts"]

#: A fact-check outcome. `unchecked` is the honest default: most claims are
#: never checked, and rendering them as "unverified" would imply an attempt that
#: never happened.
Verdict = Literal["unchecked", "supported", "unsupported", "contradicted"]


class KnowledgeNode(ContractModel):
    """One node: a party, something they claimed, a thing, or a source.

    Flat rather than a discriminated union over four shapes, because this is a
    wire contract read by TypeScript and by a graph renderer that wants uniform
    nodes. The optional fields are populated per `kind`; which ones apply is
    documented on each.
    """

    id: str
    kind: KnowledgeNodeKind
    label: str

    #: claim only — which round it was made in, and who made it.
    round: int | None = None
    author_id: str | None = None
    #: claim only — fact / value / prediction, mirroring `agent_io.ClaimKind`.
    claim_kind: str | None = None
    #: claim only — stays `unchecked` unless something actually checked it.
    verdict: Verdict | None = None
    #: evidence only — where the snippet came from.
    source_url: str | None = None


class KnowledgeEdge(ContractModel):
    """A directed, typed relation between two knowledge nodes."""

    source: str
    target: str
    kind: KnowledgeEdgeKind


class KnowledgeGraphView(ContractModel):
    """The argument so far, as a graph."""

    nodes: list[KnowledgeNode] = Field(default_factory=list)
    edges: list[KnowledgeEdge] = Field(default_factory=list)


class WhisperRecord(ContractModel):
    """One private aside, as the audience sees it.

    The asymmetry is the whole feature. This reaches the browser, because
    watching Rex promise Ada one thing and privately tell Mira the opposite is
    the most interesting thing that can happen at this table. It does *not*
    reach any agent except the recipient — see `TurnContext.whispers_to_me`.

    Dramatic irony only works if the audience knows more than the room does.
    """

    from_: str = Field(alias="from")
    to: str
    text: str
    round: int
    timestamp: str = Field(default_factory=utc_now_iso)


class NegotiationState(ContractModel):
    """The complete public state of a session.

    `closing_positions` stays `None` until the discussion ends, then carries
    each party's final position on the topic.

    `total_rounds` is carried here as well as on `round_change` so the snapshot
    is self-describing: a client that connects or resumes mid-game knows how
    long the session is before the next round tick, rather than having to
    hardcode it.
    """

    round: int
    total_rounds: int
    pool: Pool
    agents: list[AgentInfo] = Field(default_factory=list)
    trust_graph: TrustGraphView = Field(default_factory=TrustGraphView)
    #: What has been argued, as opposed to who trusts whom. Empty until parties
    #: start making claims, which only happens with a `context_topic` set.
    knowledge_graph: KnowledgeGraphView = Field(default_factory=KnowledgeGraphView)
    offer_log: list[OfferRecord] = Field(default_factory=list)
    agent_thoughts: list[AgentThought] = Field(default_factory=list)
    #: Every aside so far. Visible to whoever is watching, never to the
    #: parties it was not addressed to.
    whispers: list[WhisperRecord] = Field(default_factory=list)
    #: Current split of the pool. The number the whole discussion is about,
    #: so it belongs on screen while it is still in play.
    holdings: dict[str, float] = Field(default_factory=dict)
    closing_positions: dict[str, str] | None = None


# --------------------------------------------------------------------------- #
# Endpoint payloads
# --------------------------------------------------------------------------- #

Confidence = Literal["high", "low"]


class OfferResponseRequest(ContractModel):
    """Body for `POST /api/session/{id}/respond` — answering an offer."""

    offer_id: str
    accepted: bool


class SessionStartRequest(ContractModel):
    """Optional body for `POST /api/session/start`.

    The whole body is optional, so a bare POST still starts a plain negotiation —
    the pre-existing behaviour, and what the frontend's START button does.
    """

    context_topic: str | None = Field(
        default=None,
        max_length=500,
        description=(
            "Any real-world matter the table should argue about, in the user's own "
            "words. Passed through verbatim to every party; nothing in the prompts "
            "is specific to any subject. Also what enables the web_search tool."
        ),
    )


class SessionStartResponse(ContractModel):
    session_id: str


class TranscriptResponse(ContractModel):
    """Plain speech-to-text, with no offer parsing attached.

    Used for the spoken opening topic, which is said before a session exists.
    """

    transcript: str
    confidence: Confidence = "low"


class VoiceOfferResponse(ContractModel):
    """Returned by the voice endpoint so the frontend can preview before confirming.

    `parsed_offer` is `None` when the transcript couldn't be resolved into a
    valid offer; `confidence` tells the UI whether to require confirmation.
    """

    transcript: str
    parsed_offer: OfferSchema | None = None
    confidence: Confidence = "low"
