"""The seam between the negotiation engine and whatever is deciding moves.

The engine only ever sees `Agent`, so a scripted test double and a real LLM
call are interchangeable. This is what let the whole state machine be tested
deterministically before any model was wired in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from app.agents.opponent_model import BeliefSet
from app.models.agent_io import AgentDecision
from app.models.schemas import AgentInfo, OfferRecord, Pool

__all__ = ["PendingOffer", "TurnContext", "Agent"]


@dataclass
class PendingOffer:
    """An offer awaiting an answer.

    Carries an `id` because an agent must be able to say *which* offer it is
    accepting. The id is internal: the public `OfferRecord` has no id field, so
    this type keeps the wire contract untouched. `log_index` points at the
    record in the offer log to stamp when the offer is answered.
    """

    id: str
    round: int
    from_id: str
    to_id: str
    resource: str
    amount: float
    log_index: int

    def describe(self) -> str:
        """How the offer is presented to an agent in its prompt."""
        return (
            f"[{self.id}] {self.from_id} offers you {self.amount:g} {self.resource} "
            f"(made in round {self.round})"
        )


@dataclass
class TurnContext:
    """Everything an agent may look at when deciding its move.

    Assembled fresh each turn by the engine, so an agent holds no state of its
    own and can't accidentally see another agent's hidden objective.
    """

    agent_id: str
    round: int
    total_rounds: int
    pool: Pool
    holdings: dict[str, float]
    parties: list[AgentInfo]
    #: Only offers directed at this agent, and only unanswered ones.
    pending_offers: list[PendingOffer] = field(default_factory=list)
    #: Tail of the shared offer log, for shared situational awareness.
    recent_offers: list[OfferRecord] = field(default_factory=list)
    #: This agent's private read on everyone else.
    beliefs: BeliefSet | None = None
    #: This agent's row of the public trust graph: party id -> how much this
    #: agent trusts them. Shown next to the private belief so the model can
    #: notice where the public record and its own read diverge.
    trust_row: dict[str, float] = field(default_factory=dict)
    #: Optional real-world premise for the whole session, given to every party
    #: identically. Adds context to the same game; it does not change the rules.
    #: Its presence is also what enables the `web_search` tool for the turn.
    context_topic: str | None = None

    @property
    def my_holdings(self) -> float:
        return self.holdings.get(self.agent_id, 0.0)

    @property
    def other_party_ids(self) -> list[str]:
        return [party.id for party in self.parties if party.id != self.agent_id]

    def pending_offer_ids(self) -> list[str]:
        return [offer.id for offer in self.pending_offers]


@runtime_checkable
class Agent(Protocol):
    """Anything that can take a turn."""

    id: str

    async def decide(self, context: TurnContext) -> AgentDecision:
        """Choose a move. Must always return a valid decision, never raise."""
        ...
