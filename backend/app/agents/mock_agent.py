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
from app.models.agent_io import AgentDecision, ProposedOffer

__all__ = ["ScriptedAgent", "RandomAgent"]


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

    async def decide(self, context: TurnContext) -> AgentDecision:
        # Answer the oldest outstanding offer first, so offers don't pile up.
        if context.pending_offers:
            offer = context.pending_offers[0]
            if self._rng.random() < 0.6:
                return AgentDecision(
                    action="accept",
                    target_offer_id=offer.id,
                    thought=f"Taking {offer.amount:g} from {offer.from_id}.",
                )
            return AgentDecision(
                action="reject",
                target_offer_id=offer.id,
                thought=f"Not enough from {offer.from_id}.",
            )

        others = context.other_party_ids
        if not others or context.my_holdings <= 0 or self._rng.random() < 0.25:
            return AgentDecision(action="pass", thought="Holding position this round.")

        target = self._rng.choice(others)
        amount = round(context.my_holdings * self._rng.uniform(0.05, 0.35), 2)
        if amount <= 0:
            return AgentDecision(action="pass", thought="Nothing left to offer.")

        return AgentDecision(
            action="offer",
            offer=ProposedOffer(to=target, resource=context.pool.resource, amount=amount),
            thought=f"Testing {target} with {amount:g}.",
        )
