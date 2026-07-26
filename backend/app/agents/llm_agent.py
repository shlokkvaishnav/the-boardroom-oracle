"""The real thing: one persona, one Claude call per turn.

Structured output is requested through `output_config.format` with a JSON
Schema derived from `AgentDecision`, and the response is validated against the
same model. Using the plain `messages.create` + explicit validate path (rather
than the SDK's `messages.parse` helper) is deliberate — it puts the
malformed-response case in *this* module, where the required behaviour is:

    call -> validate -> on failure, retry ONCE with the error fed back
                     -> on second failure, fall back to a safe `pass`

A turn therefore always produces a valid decision and never raises into the
game loop. A broken model response costs the agent its turn, not the demo.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from anthropic import AsyncAnthropic
from pydantic import ValidationError

from app.agents.base import TurnContext
from app.agents.personas import PERSONAS, Persona
from app.config import Settings
from app.models.agent_io import AgentDecision

logger = logging.getLogger("boardroom.agent")

__all__ = ["LLMAgent", "build_llm_agents", "build_anthropic_client"]

#: Generated once — it's identical for every agent and every turn, which also
#: keeps the prompt prefix byte-stable for caching.
DECISION_SCHEMA: dict[str, Any] = AgentDecision.model_json_schema()


def build_anthropic_client(settings: Settings) -> AsyncAnthropic:
    """Async client. Async matters: turns must not block the event loop."""
    return AsyncAnthropic(api_key=settings.anthropic_api_key)


class LLMAgent:
    """A negotiating agent backed by a Claude call."""

    def __init__(
        self,
        persona: Persona,
        client: AsyncAnthropic,
        settings: Settings,
    ) -> None:
        self.persona = persona
        self.id = persona.id
        self._client = client
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
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": self.render_turn(context)}
        ]
        last_error = "unknown error"

        for attempt in (1, 2):
            raw = ""
            try:
                raw = await self._request(messages)
                decision = AgentDecision.model_validate_json(raw)
                return self._sanitize(decision, context)
            except (ValidationError, json.JSONDecodeError, ValueError) as exc:
                last_error = str(exc)
                logger.warning(
                    "%s produced an invalid decision (attempt %d/2): %s",
                    self.id,
                    attempt,
                    last_error,
                )
                if attempt == 1:
                    messages = self._retry_messages(messages, raw, last_error)
            except Exception as exc:  # API/transport failure
                last_error = f"{type(exc).__name__}: {exc}"
                logger.warning(
                    "%s call failed (attempt %d/2): %s", self.id, attempt, last_error
                )

        logger.error("%s falling back to pass after two failures", self.id)
        return AgentDecision.safe_default(last_error[:120])

    async def _request(self, messages: list[dict[str, Any]]) -> str:
        """One API call, returning the raw text of the response."""
        response = await self._client.messages.create(
            model=self._settings.anthropic_model,
            max_tokens=self._settings.anthropic_max_tokens,
            system=self.system_prompt(),
            messages=messages,  # type: ignore[arg-type]
            output_config={
                "format": {"type": "json_schema", "schema": DECISION_SCHEMA},
                "effort": self._settings.anthropic_effort,
            },
        )

        stop_reason = getattr(response, "stop_reason", None)
        if stop_reason == "refusal":
            raise ValueError("model declined to respond to this turn")
        if stop_reason == "max_tokens":
            raise ValueError("response hit max_tokens and is truncated")

        for block in response.content:
            if getattr(block, "type", None) == "text":
                return block.text
        raise ValueError("response contained no text block")

    @staticmethod
    def _retry_messages(
        messages: list[dict[str, Any]], raw: str, error: str
    ) -> list[dict[str, Any]]:
        """Feed the failure back and ask again.

        The conversation deliberately ends on a *user* turn: a trailing
        assistant message would be a prefill, which current models reject.
        """
        return [
            *messages,
            {"role": "assistant", "content": raw or "(empty response)"},
            {
                "role": "user",
                "content": (
                    f"That response was rejected: {error}\n\n"
                    "Reply again with a single JSON object that satisfies the schema. "
                    "Remember: `offer` is required only when action is 'offer', and "
                    "`target_offer_id` only when action is 'accept' or 'reject'. Output "
                    "nothing except the JSON object."
                ),
            },
        ]

    def _sanitize(self, decision: AgentDecision, context: TurnContext) -> AgentDecision:
        """Repair the near-misses instead of burning a turn on them.

        Two failure modes show up often enough to be worth handling rather
        than retrying: inventing a resource name, and naming a pending offer
        that doesn't exist. Both are cheap to correct and keep the demo moving.
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
    client: AsyncAnthropic,
    settings: Settings,
    personas: tuple[Persona, ...] = PERSONAS,
) -> list[LLMAgent]:
    """One agent per persona, in seating order."""
    return [LLMAgent(persona, client, settings) for persona in personas]
