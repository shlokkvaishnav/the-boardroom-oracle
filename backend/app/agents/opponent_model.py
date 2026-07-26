"""Each agent's lightweight belief state about every other party.

This is the second rule that gets explained live, so it follows the same shape
as the trust graph: one tunable constant, one pure helper, and one named
method per observation.

Everything is an exponential moving average — recent behaviour counts for more
than old behaviour, with no growing history to store:

    updated = (1 - ALPHA) * previous + ALPHA * observation

ALPHA = 0.4 means a single event moves a belief roughly 40% of the way toward
what it just saw, so an agent adapts within two or three rounds — fast enough
to be visible across a six-round demo, slow enough not to look random.

Note the division of labour with the trust graph: the graph is the *public
record*, computed by a fixed rule and drawn in the UI. `trust_score` here is
the agent's own *private, subjective* running score, moved only by the deltas
that agent itself reports. Both are shown in the prompt, and the gap between
them is often the most interesting thing on screen.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["ALPHA", "ema", "clamp01", "OpponentBelief", "BeliefSet"]

#: How heavily the newest observation counts. 0 = never learn, 1 = only remember the last event.
ALPHA = 0.4

#: Beliefs start neutral: no assumption of good or bad faith.
NEUTRAL = 0.5


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def ema(previous: float, observation: float, alpha: float = ALPHA) -> float:
    """Blend a new observation into a running average."""
    return (1.0 - alpha) * previous + alpha * observation


@dataclass
class OpponentBelief:
    """What one agent currently believes about one other party."""

    agent_id: str

    #: How often they accept offers I make them. 1.0 = always says yes.
    acceptance_rate: float = NEUTRAL
    #: How stingy their offers to me are. 1.0 = offers me nothing.
    perceived_aggressiveness: float = NEUTRAL
    #: This agent's own running trust score, moved only by deltas it reports
    #: itself. Subjective by design — the trust graph holds the public record.
    trust_score: float = NEUTRAL

    #: Raw counters, kept for the UI and for explaining the rates out loud.
    offers_i_made: int = 0
    offers_they_accepted: int = 0
    offers_they_made_me: int = 0

    def observe_response_to_my_offer(self, accepted: bool) -> None:
        """They answered one of my offers — update how agreeable they look."""
        self.offers_i_made += 1
        if accepted:
            self.offers_they_accepted += 1
        self.acceptance_rate = round(ema(self.acceptance_rate, 1.0 if accepted else 0.0), 4)

    def observe_their_offer(self, favorability: float) -> None:
        """They made me an offer — a generous one reads as less aggressive.

        `favorability` is the offer as a fraction of the pool, so aggressiveness
        is simply its complement.
        """
        self.offers_they_made_me += 1
        self.perceived_aggressiveness = round(
            ema(self.perceived_aggressiveness, 1.0 - favorability), 4
        )

    def apply_reported_delta(self, delta: float) -> None:
        """Fold in a trust change this agent reported at the end of its own turn.

        Halved before applying so a single dramatic self-report can't swing the
        score end to end, and clamped to [0, 1] like every other trust number.
        """
        self.trust_score = round(clamp01(self.trust_score + 0.5 * delta), 4)

    def summary(self, public_trust: float | None = None) -> str:
        """One line, rendered into the agent's next prompt."""
        public = "" if public_trust is None else f", public_trust={public_trust:.2f}"
        return (
            f"{self.agent_id}: my_trust={self.trust_score:.2f}{public}, "
            f"accepts_my_offers={self.acceptance_rate:.2f}, "
            f"aggressiveness={self.perceived_aggressiveness:.2f} "
            f"(they accepted {self.offers_they_accepted}/{self.offers_i_made} of my offers; "
            f"they have made me {self.offers_they_made_me})"
        )


@dataclass
class BeliefSet:
    """One agent's beliefs about everyone else."""

    owner_id: str
    beliefs: dict[str, OpponentBelief] = field(default_factory=dict)

    @classmethod
    def for_agent(cls, owner_id: str, party_ids: list[str]) -> BeliefSet:
        return cls(
            owner_id=owner_id,
            beliefs={
                party: OpponentBelief(agent_id=party) for party in party_ids if party != owner_id
            },
        )

    def about(self, party_id: str) -> OpponentBelief:
        """Beliefs about one party, created on demand if the party is new."""
        if party_id not in self.beliefs:
            self.beliefs[party_id] = OpponentBelief(agent_id=party_id)
        return self.beliefs[party_id]

    def render(self, public_trust: dict[str, float] | None = None) -> str:
        """The block appended to the agent's next LLM call.

        This is the mechanism that makes behaviour visibly adapt round over
        round: the model sees what it learned last round as plain text.
        `public_trust` is the trust-graph row for this agent, shown alongside
        its private score so the model can notice when the two disagree.
        """
        if not self.beliefs:
            return "(no reads on anyone yet)"
        public_trust = public_trust or {}
        return "\n".join(
            self.beliefs[party].summary(public_trust.get(party))
            for party in sorted(self.beliefs)
        )
