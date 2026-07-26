"""LLM-free agents, used to test the engine deterministically.

`ScriptedAgent` replays an exact list of moves, which is what makes turn
order, the offer log, and round transitions assertable without touching a
model. `RandomAgent` plays plausible but seeded moves, for exercising long
runs and the endgame.

Both satisfy `agents.base.Agent`, so the engine cannot tell them apart from
the real thing.
"""

from __future__ import annotations

import random

from app.agents.base import TurnContext
from app.models.agent_io import AgentDecision, Claim, ProposedOffer

__all__ = ["ScriptedAgent", "RandomAgent", "topic_entities"]

#: Claim shapes for the keyless demo, one per kind so the UI shows all three.
#:
#: Content-free on purpose: a mock agent has no opinion, and inventing plausible
#: prose about the user's real topic would put words in its mouth that read as
#: analysis. These are obviously scaffolding, and still exercise the graph.
_CLAIM_TEMPLATES: tuple[tuple[str, str], ...] = (
    ("{entity} is the constraint everything else follows from.", "value"),
    ("{entity} has not recovered to where it was.", "fact"),
    ("{entity} gets worse before it gets better.", "prediction"),
    ("Whoever moves last on {entity} pays for it.", "prediction"),
)

#: Words too common to be worth a node of their own.
_STOPWORDS = frozenset(
    "the a an of and or to in on for with at by from is are was were be been "
    "this that these those it its as after before over under new".split()
)


def topic_entities(topic: str | None, limit: int = 3) -> list[str]:
    """The few words in a topic worth treating as things.

    Crude on purpose — this exists so the keyless demo has *something* to draw,
    not to do entity recognition. Real extraction is the model's job, and it
    reports entities itself on every claim.
    """
    if not topic:
        return []
    words = [w.strip(".,;:!?'\"()") for w in topic.split()]
    return [w for w in words if len(w) > 3 and w.lower() not in _STOPWORDS][:limit]


class ScriptedAgent:
    """Replays `script` in order, then passes forever.

    Running off the end of the script is deliberately not an error: tests
    usually care about the first few turns and shouldn't have to pad.
    """

    def __init__(self, agent_id: str, script: list[AgentDecision] | None = None) -> None:
        self.id = agent_id
        self._script = list(script or [])
        self._turn = 0
        #: Every context this agent was handed, for asserting what it was shown.
        self.seen_contexts: list[TurnContext] = []

    async def decide(self, context: TurnContext) -> AgentDecision:
        self.seen_contexts.append(context)
        if self._turn < len(self._script):
            decision = self._script[self._turn]
            self._turn += 1
            return decision
        return AgentDecision(action="pass", thought="(scripted agent out of moves)")

    @property
    def turns_taken(self) -> int:
        return len(self.seen_contexts)


class RandomAgent:
    """Plausible seeded play: answers pending offers, otherwise offers or passes.

    Seeded so a full six-round game is reproducible in tests.
    """

    def __init__(self, agent_id: str, seed: int = 0) -> None:
        self.id = agent_id
        self._rng = random.Random(seed)

    def _claims(self, context: TurnContext) -> list[Claim]:
        """A scaffolding claim, so a keyless run still fills the knowledge graph.

        Only with a topic set, which is the same condition the real agents claim
        under. Every agent draws entities from the same topic, so the graph shows
        its defining behaviour — several parties' claims converging on one shared
        entity node — rather than three disconnected islands.
        """
        entities = topic_entities(context.context_topic)
        if not entities or self._rng.random() < 0.45:
            return []
        template, kind = self._rng.choice(_CLAIM_TEMPLATES)
        entity = self._rng.choice(entities)
        return [Claim(text=template.format(entity=entity), kind=kind, entities=[entity])]

    def _stance(self, context: TurnContext) -> float | None:
        """A stance that drifts, so the keyless demo has a chart to draw.

        Anchored per agent and nudged each round, rather than random per
        turn: a line that wanders at random looks like noise, and the whole
        point of the chart is to show whether anyone actually moved.
        """
        if not context.context_topic:
            return None
        anchor = {"cooperator": 0.4, "maximizer": -0.7, "titfortat": 0.0}.get(self.id, 0.0)
        drift = (context.round / max(1, context.total_rounds)) * 0.5
        return max(-1.0, min(1.0, anchor + drift * (1 if anchor < 0 else -1)))

    async def decide(self, context: TurnContext) -> AgentDecision:
        claims = self._claims(context)
        stance = self._stance(context)

        # Answer the oldest outstanding offer first, so offers don't pile up.
        if context.pending_offers:
            offer = context.pending_offers[0]
            if self._rng.random() < 0.6:
                return AgentDecision(
                    action="accept",
                    target_offer_id=offer.id,
                    thought=f"Taking {offer.amount:g} from {offer.from_id}.",
                    claims=claims,
                    stance=stance,
                )
            return AgentDecision(
                action="reject",
                target_offer_id=offer.id,
                thought=f"Not enough from {offer.from_id}.",
                claims=claims,
                stance=stance,
            )

        others = context.other_party_ids
        if not others or context.my_holdings <= 0 or self._rng.random() < 0.25:
            return AgentDecision(
                action="pass",
                thought="Holding position this round.",
                claims=claims,
                stance=stance,
            )

        target = self._rng.choice(others)
        amount = round(context.my_holdings * self._rng.uniform(0.05, 0.35), 2)
        if amount <= 0:
            return AgentDecision(action="pass", thought="Nothing left to offer.")

        return AgentDecision(
            action="offer",
            offer=ProposedOffer(to=target, resource=context.pool.resource, amount=amount),
            thought=f"Testing {target} with {amount:g}.",
            claims=claims,
            stance=stance,
        )
