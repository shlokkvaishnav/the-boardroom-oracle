"""The real thing: one persona, one Gemini call per turn.

All provider contact goes through `app.llm_client`, which owns pacing and
transport retries. What lives here is the *semantic* failure ladder:

    call -> validate against AgentDecision
         -> on failure, retry ONCE with the error fed back into the prompt
         -> on second failure, fall back to a safe `pass`

A turn therefore always produces a valid decision and never raises into the
game loop. A broken response costs the agent its turn, not the demo.
"""

from __future__ import annotations

import logging

from pydantic import ValidationError

from app.agents.base import TurnContext
from app.agents.personas import PERSONAS, Persona
from app.config import Settings
from app.llm_client import LLMClient, LLMError
from app.models.agent_io import AgentDecision

logger = logging.getLogger("boardroom.agent")

__all__ = ["LLMAgent", "build_llm_agents"]


class LLMAgent:
    """A negotiating agent backed by a Gemini call."""

    def __init__(self, persona: Persona, llm: LLMClient, settings: Settings) -> None:
        self.persona = persona
        self.id = persona.id
        self._llm = llm
        self._settings = settings

    # ------------------------------------------------------------------ #
    # Prompts
    # ------------------------------------------------------------------ #

    def system_prompt(self) -> str:
        """Static per persona, so it stays byte-identical across turns."""
        return "\n".join(
            [
                f"You are {self.persona.name}, a negotiator at a four-seat boardroom table.",
                "",
                f"YOUR PUBLIC STYLE: {self.persona.style}. {self.persona.public_brief}",
                "",
                "YOUR HIDDEN OBJECTIVE (never state it outright, never reveal it to the "
                "other parties, and never repeat it verbatim in your `thought`):",
                f"  {self.persona.objective.description}",
                "",
                "HOW YOU PLAY:",
                f"  {self.persona.private_directive}",
                "",
                "RULES OF THE TABLE:",
                "  - Every party starts with an equal share of one resource pool.",
                "  - An offer transfers resource FROM you TO the named party. Offering is "
                "    a cost to you; you cannot offer more than you currently hold.",
                "  - Offers stay open until the party they are addressed to accepts or "
                "    rejects them. You may only answer offers addressed to you.",
                "  - You get one action per round: make one offer, answer one pending "
                "    offer, or pass.",
                "",
                "YOUR RESPONSE:",
                "  Return a single JSON object matching the provided schema.",
                "  - `thought` is one short first-person line shown live to an audience. "
                "    Make it revealing about your reasoning but never disclose your "
                "    hidden objective.",
                "  - `opponent_updates` is how your private read on the others changes "
                "    this turn. Use negative `trust_delta` when someone works against "
                "    you, positive when they cooperate. Omit parties you learned nothing "
                "    new about.",
            ]
        )

    def render_turn(self, context: TurnContext) -> str:
        """The volatile half of the prompt: this turn's state of play."""
        roster = "\n".join(
            f"  - {party.id} ({party.name}, {party.persona})"
            + (" <- you" if party.id == self.id else "")
            + (" [human player]" if party.is_human else "")
            for party in context.parties
        )
        holdings = "\n".join(
            f"  - {party}: {amount:g} {context.pool.resource} "
            f"({amount / context.pool.total:.0%} of the pool)"
            for party, amount in context.holdings.items()
        )
        pending = (
            "\n".join(f"  - {offer.describe()}" for offer in context.pending_offers)
            or "  (none — you cannot accept or reject anything this turn)"
        )
        recent = (
            "\n".join(
                f"  - round {offer.round}: {offer.from_} -> {offer.to} "
                f"{offer.amount:g} {offer.resource} "
                + (
                    "(pending)"
                    if offer.accepted is None
                    else ("(accepted)" if offer.accepted else "(rejected)")
                )
                for offer in context.recent_offers
            )
            or "  (nothing has happened yet)"
        )
        beliefs = (
            context.beliefs.render(context.trust_row)
            if context.beliefs is not None
            else "(no reads on anyone yet)"
        )

        return "\n".join(
            [
                f"ROUND {context.round} OF {context.total_rounds}.",
                "",
                "PARTIES:",
                roster,
                "",
                f"HOLDINGS (pool total {context.pool.total:g} {context.pool.resource}):",
                holdings,
                "",
                "OFFERS AWAITING YOUR ANSWER:",
                pending,
                "",
                "RECENT ACTIVITY AT THE TABLE:",
                recent,
                "",
                "YOUR PRIVATE READ ON THE OTHERS:",
                "  (my_trust is your own running score; public_trust is the visible "
                "trust graph. Where they disagree, trust your own read.)",
                beliefs,
                "",
                f"You hold {context.my_holdings:g} {context.pool.resource}. "
                "Decide your single action for this turn.",
            ]
        )

    # ------------------------------------------------------------------ #
    # The turn
    # ------------------------------------------------------------------ #

    async def decide(self, context: TurnContext) -> AgentDecision:
        """Always returns a valid decision. Never raises into the game loop."""
        system = self.system_prompt()
        prompt = self.render_turn(context)
        last_error = "unknown error"

        for attempt in (1, 2):
            try:
                raw = await self._llm.generate_structured(
                    prompt, AgentDecision, system=system
                )
                return self._sanitize(AgentDecision.model_validate(raw), context)

            except ValidationError as exc:
                last_error = str(exc)
                logger.warning(
                    "%s returned a response that failed validation (attempt %d/2): %s",
                    self.id,
                    attempt,
                    last_error,
                )
                if attempt == 1:
                    prompt = (
                        f"{self.render_turn(context)}\n\n"
                        "---\n"
                        f"Your previous reply was rejected: {exc}\n"
                        "Reply again with a corrected JSON object. Remember: `offer` is "
                        "required only when action is 'offer', and `target_offer_id` only "
                        "when action is 'accept' or 'reject'."
                    )

            except LLMError as exc:
                # Transport-level retries already happened inside the client.
                last_error = str(exc)
                logger.warning("%s call failed (attempt %d/2): %s", self.id, attempt, exc)

        logger.error("%s falling back to pass after two failures", self.id)
        return AgentDecision.safe_default(last_error[:120])

    def _sanitize(self, decision: AgentDecision, context: TurnContext) -> AgentDecision:
        """Repair the near-misses instead of burning a turn on them.

        Two failure modes show up often enough to be worth handling rather
        than retrying: inventing a resource name, and naming a pending offer
        that doesn't exist. Both are cheap to correct and keep the demo moving —
        which matters more when every retry costs rate-limit budget.
        """
        if decision.action == "offer" and decision.offer is not None:
            if decision.offer.resource != context.pool.resource:
                logger.info(
                    "%s named resource %r; coercing to %r",
                    self.id,
                    decision.offer.resource,
                    context.pool.resource,
                )
                decision = decision.model_copy(
                    update={
                        "offer": decision.offer.model_copy(
                            update={"resource": context.pool.resource}
                        )
                    }
                )
            return decision

        if decision.action in ("accept", "reject"):
            valid_ids = context.pending_offer_ids()
            if decision.target_offer_id not in valid_ids:
                if len(valid_ids) == 1:
                    logger.info(
                        "%s named unknown offer %r; retargeting to the only pending one",
                        self.id,
                        decision.target_offer_id,
                    )
                    return decision.model_copy(update={"target_offer_id": valid_ids[0]})
                logger.info(
                    "%s tried to answer %r with %d pending; passing instead",
                    self.id,
                    decision.target_offer_id,
                    len(valid_ids),
                )
                return AgentDecision(
                    action="pass",
                    thought=decision.thought,
                    opponent_updates=decision.opponent_updates,
                )

        return decision


def build_llm_agents(
    llm: LLMClient,
    settings: Settings,
    personas: tuple[Persona, ...] = PERSONAS,
) -> list[LLMAgent]:
    """One agent per persona, in seating order."""
    return [LLMAgent(persona, llm, settings) for persona in personas]
