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
from app.models.agent_io import AgentDecision, TurnDecision
from app.models.schemas import SearchRecord
from app.search import TOOL_NAME, WEB_SEARCH_TOOL, SearchError, WebSearchTool

logger = logging.getLogger("boardroom.agent")

__all__ = ["LLMAgent", "build_llm_agents", "MAX_CLAIMS"]

#: Claims kept from one turn. Matches what the prompt asks for; enforced here
#: because a turn that argues two points well is the shape we want, and a turn
#: listing six is padding that also bloats the response toward the token cap.
MAX_CLAIMS = 2


class LLMAgent:
    """A negotiating agent backed by a Gemini call."""

    def __init__(
        self,
        persona: Persona,
        llm: LLMClient,
        settings: Settings,
        search: WebSearchTool | None = None,
    ) -> None:
        self.persona = persona
        self.id = persona.id
        self._llm = llm
        self._settings = settings
        self._search = search

    # ------------------------------------------------------------------ #
    # Prompts
    # ------------------------------------------------------------------ #

    def system_prompt(self, context_topic: str | None = None) -> str:
        """Static per persona *and* per session, so it stays byte-identical across turns.

        `context_topic` is a session-level premise, not a per-turn value, so it
        belongs here rather than in `render_turn` — it stays stable for the
        whole game and keeps the volatile half of the prompt volatile.
        """
        premise = (
            [
                "THE MATTER ON THE TABLE:",
                f"  {context_topic}",
                "",
                "  This is a real discussion, and it is the point of the session. Take a "
                "  clear position on it — one that follows from who you are — and argue "
                "  it in concrete terms: consequences, precedent, who bears the cost, "
                "  what happens next. Disagree with the others where you genuinely do. "
                "  A room where everyone is agreeable and vague is a failed session.",
                "",
                "  The budget you are splitting is what the argument is *over*, not what "
                "  the argument is *about*. Never make the money itself your subject: "
                "  nobody wants to hear that you are 'building goodwill' or 'testing' "
                "  someone. Say what you actually think about the matter above.",
                "",
            ]
            if context_topic
            else []
        )
        return "\n".join(
            [
                f"You are {self.persona.name}, a negotiator at a four-seat boardroom table.",
                "",
                *premise,
                f"YOUR PUBLIC STYLE: {self.persona.style}. {self.persona.public_brief}",
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
                "  - `thought` is what you SAY OUT LOUD at the table, heard by everyone "
                "    and shown live to an audience. It is an argument, not a caption. "
                "    Do NOT narrate your move ('I'll offer 2 to test her') — the offer "
                "    is already visible. Say the *substance*: what you believe about "
                "    the matter on the table, and why the others should move your way. "
                "    Name whoever spoke last and answer them — agree, rebut, or press "
                "    them on something they dodged.",
                "  - HOW TO TALK. Like a person in a room, not an op-ed. Short "
                "    sentences. Everyday words — say 'costs too much', not "
                "    'imposes prohibitive fiscal burdens'. Use contractions. One "
                "    sentence is usually plenty, two is the limit. If a plainer "
                "    word exists, use it. No throat-clearing, no 'furthermore', no "
                "    stacking three clauses into one breath. Read it back: if it "
                "    sounds like a press release, rewrite it.",
                "  - Two things that ruin it, so avoid both. Do not append your reason "
                "    for the move ('...so I am mirroring her cooperation') — that is "
                "    narration, and nobody speaks that way. Do not repeat someone's "
                "    sentence back at them; if you agree, say so in one clause and then "
                "    add the point they missed.",
                "  - `opponent_updates` is how your private read on the others changes "
                "    this turn. Use negative `trust_delta` when someone works against "
                "    you, positive when they cooperate. Omit parties you learned nothing "
                "    new about.",
                "  - `claims` is the point underneath what you just said, written out "
                "    plainly so it can be recorded and checked. At most two, and often "
                "    none: leave it empty if you only agreed, asked a question, or made "
                "    a move without arguing for it. Two rules. Claim only things about "
                "    the matter under discussion — never about the negotiation itself, "
                "    so 'the deadline is the real constraint' yes, 'Rex is stonewalling' "
                "    no. And mark each one honestly: `fact` if someone could look it up, "
                "    `prediction` if it is about what happens next, `value` if it is a "
                "    judgement about what matters. Do not dress a value up as a fact — "
                "    what you claim as fact may be checked against a source, and being "
                "    caught inventing one costs you the argument.",
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
        by_id = {party.id: party.name for party in context.parties}
        remarks = (
            "\n".join(
                f"  {by_id.get(remark.agent_id, remark.agent_id)}"
                + (" (you)" if remark.agent_id == self.id else "")
                + f": \"{remark.text}\""
                for remark in context.recent_remarks
            )
            or "  (nobody has spoken yet — you are opening the discussion)"
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
                "WHAT HAS BEEN SAID (answer it — do not talk past it):",
                remarks,
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

    async def decide(self, context: TurnContext) -> TurnDecision:
        """Always returns a valid decision. Never raises into the game loop."""
        system = self.system_prompt(context.context_topic)
        searched, calls = await self._maybe_search(context, system)
        prompt = self._turn_prompt(context, searched)
        last_error = "unknown error"

        for attempt in (1, 2):
            try:
                calls += 1
                raw = await self._llm.generate_structured(
                    prompt, AgentDecision, system=system
                )
                decision = self._sanitize(AgentDecision.model_validate(raw), context)
                return TurnDecision.of(decision, searched=searched, llm_calls=calls)

            except ValidationError as exc:
                last_error = str(exc)
                logger.warning(
                    "%s returned a response that failed validation (attempt %d/2): %s",
                    self.id,
                    attempt,
                    last_error,
                )
                if attempt == 1:
                    # Rebuilt via _turn_prompt so the retry keeps any search
                    # results — dropping them would make the second attempt
                    # reason from less than the first.
                    prompt = (
                        f"{self._turn_prompt(context, searched)}\n\n"
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
        return TurnDecision.of(
            AgentDecision.safe_default(last_error[:120]),
            searched=searched,
            llm_calls=calls,
        )

    # ------------------------------------------------------------------ #
    # Phase one: does this turn need a fact?
    # ------------------------------------------------------------------ #

    async def _maybe_search(
        self, context: TurnContext, system: str
    ) -> tuple[list[SearchRecord], int]:
        """Offer the tool, and run one search if the model asks for it.

        Returns the provenance records and how many provider calls were spent.
        Gated on the session having a `context_topic`: without one this returns
        immediately and the turn is exactly the single structured call it has
        always been, with no tools sent and nothing extra paid.

        The one-search-per-turn cap is structural — this runs once per turn and
        honours a single call — rather than a limit the prompt asks the model to
        respect. Every failure path here degrades to "no search", never to a
        lost turn.
        """
        if not context.context_topic or self._search is None:
            return [], 0
        if not context.allow_search:
            # The session's budget has no surplus above what finishing costs.
            # Degrade to the plain single-call turn rather than risk a session
            # that never reaches its closing.
            logger.info("%s: skipping search, no budget surplus this turn", self.id)
            return [], 0

        try:
            request = await self._llm.generate_with_tools(
                self._turn_prompt(context, []), [WEB_SEARCH_TOOL], system=system
            )
        except LLMError as exc:
            logger.warning("%s: search probe failed, continuing without it: %s", self.id, exc)
            return [], 1

        if request is None:
            return [], 1
        if request.name != TOOL_NAME:
            logger.warning("%s asked for an unknown tool %r", self.id, request.name)
            return [], 1

        query = str(request.args.get("query") or "").strip()
        if not query:
            logger.warning("%s asked to search with no query", self.id)
            return [], 1

        try:
            hits = await self._search.search(query)
        except SearchError as exc:
            logger.warning("%s: web_search %r failed: %s", self.id, query, exc)
            return [], 1

        logger.info("%s searched %r -> %d hit(s)", self.id, query, len(hits))
        return (
            [
                SearchRecord(query=query, result_snippet=hit.snippet, source_url=hit.url)
                for hit in hits
            ],
            1,
        )

    def _turn_prompt(self, context: TurnContext, searched: list[SearchRecord]) -> str:
        """This turn's state of play, plus any search results it earned."""
        prompt = self.render_turn(context)
        if not searched:
            return prompt
        hits = "\n".join(
            f"- {record.result_snippet}\n  source: {record.source_url}" for record in searched
        )
        return (
            f"{prompt}\n\n"
            f"WHAT YOU LOOKED UP (results of your `{TOOL_NAME}` call for "
            f"{searched[0].query!r}):\n{hits}\n"
            "Use these facts where they support your position. They are current; "
            "your own knowledge may not be."
        )

    def _sanitize(self, decision: AgentDecision, context: TurnContext) -> AgentDecision:
        """Repair the near-misses instead of burning a turn on them.

        Three failure modes show up often enough to be worth handling rather
        than retrying: inventing a resource name, naming a pending offer that
        doesn't exist, and returning more claims than asked for. All are cheap
        to correct and keep the demo moving — which matters more when every
        retry costs rate-limit budget.
        """
        # The prompt asks for at most two. Enforcing that by validation would
        # turn a slightly over-eager turn into a retry, which costs a whole
        # extra call to fix something a slice fixes for free.
        if len(decision.claims) > MAX_CLAIMS:
            logger.info(
                "%s returned %d claims; keeping the first %d",
                self.id,
                len(decision.claims),
                MAX_CLAIMS,
            )
            decision = decision.model_copy(
                update={"claims": decision.claims[:MAX_CLAIMS]}
            )

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
    search: WebSearchTool | None = None,
) -> list[LLMAgent]:
    """One agent per persona, in seating order."""
    return [LLMAgent(persona, llm, settings, search=search) for persona in personas]
