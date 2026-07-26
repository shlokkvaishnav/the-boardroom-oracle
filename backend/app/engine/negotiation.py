"""The turn-based negotiation state machine.

Framework-agnostic on purpose: this module knows nothing about FastAPI or
WebSockets. It emits typed messages through an injected async callback, which
is what lets the whole game be driven and asserted in tests with no server
running and no LLM involved.

Shape of a game:

    for each round 1..N:
        emit round_change
        for each agent in seating order:
            build a fresh TurnContext  (under the lock)
            await agent.decide(...)    (outside the lock — may be a slow LLM call)
            apply the decision         (under the lock)
    emit reveal

Human offers don't take a turn of their own. They land in the pending-offer
queue the moment they're injected, so the very next agent to act sees the offer
in its context and can accept, reject, or ignore it exactly like any other.
"""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Awaitable, Callable, Sequence

from app.agents.base import Agent, PendingOffer, TurnContext
from app.agents.opponent_model import BeliefSet
from app.agents.personas import PERSONAS, Persona, all_agent_infos
from app.config import Settings
from app.engine.scoring import revealed_objectives, score_all
from app.engine.trust_graph import TrustGraph, favorability
from app.models.agent_io import AgentDecision
from app.models.messages import (
    GraphUpdateMessage,
    GraphUpdatePayload,
    OfferMessage,
    RevealMessage,
    RevealPayload,
    RoundChangeMessage,
    RoundChangePayload,
    ThoughtMessage,
    WSMessage,
)
from app.models.schemas import (
    AgentThought,
    NegotiationState,
    OfferRecord,
    OfferSchema,
    Pool,
)

logger = logging.getLogger("boardroom.engine")

__all__ = ["EventEmitter", "OfferRejected", "NegotiationEngine"]

#: How the engine talks to the outside world.
EventEmitter = Callable[[WSMessage], Awaitable[None]]

#: How much of the shared log an agent is shown.
RECENT_OFFER_WINDOW = 8


async def _noop_emitter(message: WSMessage) -> None:
    """Default sink, so the engine runs happily with nobody listening."""
    return None


class OfferRejected(ValueError):
    """A human-supplied offer failed validation. Surfaces to the API as a 400."""


class NegotiationEngine:
    """One negotiation session.

    Single concurrent session is all the demo needs, but nothing here is a
    singleton — the API layer holds instances in a `SessionStore`.
    """

    def __init__(
        self,
        *,
        session_id: str,
        agents: Sequence[Agent],
        settings: Settings,
        emit: EventEmitter | None = None,
        personas: tuple[Persona, ...] = PERSONAS,
        context_topic: str | None = None,
    ) -> None:
        self.session_id = session_id
        self._agents = list(agents)
        self._settings = settings
        self._emit_cb: EventEmitter = emit or _noop_emitter
        self._personas = personas
        #: Shared real-world premise, handed to every party identically.
        self.context_topic = (context_topic or "").strip() or None

        self.total_rounds = settings.rounds
        self.pool = Pool(resource=settings.pool_resource, total=settings.pool_total)
        self.parties = all_agent_infos()
        party_ids = [party.id for party in self.parties]

        # The pool starts split evenly. Every subsequent move is a transfer, so
        # the total is conserved and shares always sum to 1.
        even_share = settings.pool_total / len(party_ids) if party_ids else 0.0
        self.holdings: dict[str, float] = {pid: round(even_share, 4) for pid in party_ids}

        self.graph = TrustGraph([(party.id, party.name) for party in self.parties])
        self.beliefs: dict[str, BeliefSet] = {
            agent.id: BeliefSet.for_agent(agent.id, party_ids) for agent in self._agents
        }

        self.round = 0
        self.offer_log: list[OfferRecord] = []
        self.thoughts: list[AgentThought] = []
        self.pending: dict[str, PendingOffer] = {}
        self.finished = False
        self.reveal: RevealPayload | None = None

        self._revealed: dict[str, str] | None = None
        self._offer_seq = 0
        #: Provider calls spent in the current round; logged when it ends.
        self._round_llm_calls = 0
        self._stopped = False
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------ #
    # Reads
    # ------------------------------------------------------------------ #

    def snapshot(self) -> NegotiationState:
        """The full public state, in the frontend's shape."""
        return NegotiationState(
            round=self.round,
            pool=self.pool,
            agents=list(self.parties),
            trust_graph=self.graph.view(),
            offer_log=list(self.offer_log),
            agent_thoughts=list(self.thoughts),
            revealed_objectives=self._revealed,
        )

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def start(self) -> asyncio.Task[None]:
        """Kick off the round loop in the background."""
        if self._task is not None:
            raise RuntimeError("session already started")
        self._task = asyncio.create_task(self.run(), name=f"negotiation-{self.session_id}")
        return self._task

    async def stop(self) -> None:
        """Halt the loop and wait for it to unwind. Safe to call more than once."""
        self._stopped = True
        task = self._task
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def run(self) -> None:
        """Play the game to completion. Normally driven by `start()`."""
        try:
            for round_number in range(1, self.total_rounds + 1):
                if self._stopped:
                    return
                async with self._lock:
                    self.round = round_number
                await self._emit(
                    RoundChangeMessage(
                        payload=RoundChangePayload(
                            round=round_number, total_rounds=self.total_rounds
                        )
                    )
                )

                self._round_llm_calls = 0
                for agent in self._agents:
                    if self._stopped:
                        return
                    await self._take_turn(agent)
                    if self._settings.turn_delay_seconds > 0:
                        await asyncio.sleep(self._settings.turn_delay_seconds)

                # Worth logging because a search-heavy round costs twice the
                # calls of a plain one, and the free tier is a per-minute
                # budget — this is the number to watch in demo rehearsal.
                logger.info(
                    "round %d of session %s used %d provider call(s)",
                    round_number,
                    self.session_id,
                    self._round_llm_calls,
                )

            await self._finish()
        except asyncio.CancelledError:
            logger.info("session %s cancelled", self.session_id)
            raise

    # ------------------------------------------------------------------ #
    # Turns
    # ------------------------------------------------------------------ #

    async def _take_turn(self, agent: Agent) -> None:
        async with self._lock:
            context = self._build_context(agent.id)

        # Deliberately outside the lock: a real agent turn is a network call,
        # and holding the lock across it would block human injection for
        # seconds at a time.
        decision = await agent.decide(context)

        async with self._lock:
            self._round_llm_calls += getattr(decision, "llm_calls", 1)
            events = self._apply(agent.id, decision)

        for event in events:
            await self._emit(event)

    def _build_context(self, agent_id: str) -> TurnContext:
        return TurnContext(
            agent_id=agent_id,
            round=self.round,
            total_rounds=self.total_rounds,
            pool=self.pool,
            holdings=dict(self.holdings),
            parties=list(self.parties),
            pending_offers=[
                offer for offer in self.pending.values() if offer.to_id == agent_id
            ],
            recent_offers=list(self.offer_log[-RECENT_OFFER_WINDOW:]),
            beliefs=self.beliefs.get(agent_id),
            trust_row=self.graph.trust_toward(agent_id, self.graph.party_ids),
            context_topic=self.context_topic,
        )

    def _apply(self, agent_id: str, decision: AgentDecision) -> list[WSMessage]:
        """Fold one decision into the state. Caller must hold the lock."""
        events: list[WSMessage] = []

        # `searched` is only present on a TurnDecision — mock agents return a
        # plain AgentDecision — so it's read defensively rather than required.
        thought = AgentThought(
            agent_id=agent_id,
            text=decision.thought,
            searched=list(getattr(decision, "searched", []) or []),
        )
        self.thoughts.append(thought)
        events.append(ThoughtMessage(payload=thought))

        if decision.action == "offer" and decision.offer is not None:
            events.extend(
                self._record_offer(
                    sender=agent_id,
                    receiver=decision.offer.to,
                    resource=decision.offer.resource,
                    amount=decision.offer.amount,
                    strict=False,
                )
            )
        elif decision.action in ("accept", "reject"):
            events.extend(
                self._answer_offer(
                    responder=agent_id,
                    offer_id=decision.target_offer_id or "",
                    accepted=decision.action == "accept",
                )
            )

        # The agent's own reported belief changes about the others.
        belief_set = self.beliefs.get(agent_id)
        if belief_set is not None:
            for update in decision.opponent_updates:
                if update.agent_id != agent_id:
                    belief_set.about(update.agent_id).apply_reported_delta(update.trust_delta)

        return events

    # ------------------------------------------------------------------ #
    # Offers
    # ------------------------------------------------------------------ #

    def _validate_offer(
        self,
        sender: str,
        receiver: str,
        resource: str,
        amount: float,
        *,
        strict: bool,
    ) -> float | None:
        """Check an offer, returning the amount to actually use.

        `strict` is the difference between a human and an agent. A human typed
        this, so a bad offer is a clear 400 rather than a silent correction. An
        LLM may hallucinate an amount it doesn't hold, so its offer is clamped
        to what it actually has and the game keeps moving.

        Returns `None` when a non-strict offer can't be salvaged.
        """

        def fail(reason: str) -> None:
            if strict:
                raise OfferRejected(reason)
            logger.warning("dropping invalid agent offer from %s: %s", sender, reason)

        if receiver not in self.holdings:
            fail(f"unknown recipient {receiver!r}")
            return None
        if receiver == sender:
            fail("cannot make an offer to yourself")
            return None
        if resource != self.pool.resource:
            fail(f"unknown resource {resource!r}; this table trades {self.pool.resource!r}")
            return None
        if not math.isfinite(amount):
            fail("amount must be a finite number")
            return None
        if amount <= 0:
            fail("amount must be greater than zero")
            return None

        available = self.holdings.get(sender, 0.0)
        if amount > available:
            if strict:
                raise OfferRejected(
                    f"{sender} holds only {available:g} {resource} and cannot offer {amount:g}"
                )
            # You can't give what you don't have.
            logger.info("clamping %s's offer from %g to %g", sender, amount, available)
            amount = available

        if amount <= 0:
            fail("nothing left to offer")
            return None

        return round(amount, 4)

    def _record_offer(
        self,
        *,
        sender: str,
        receiver: str,
        resource: str,
        amount: float,
        strict: bool,
    ) -> list[WSMessage]:
        """Log a new offer and mark it pending. Caller must hold the lock."""
        checked = self._validate_offer(sender, receiver, resource, amount, strict=strict)
        if checked is None:
            return []

        record = OfferRecord(
            round=self.round,
            from_=sender,
            to=receiver,
            resource=resource,
            amount=checked,
        )
        self.offer_log.append(record)

        self._offer_seq += 1
        offer_id = f"o{self._offer_seq}"
        self.pending[offer_id] = PendingOffer(
            id=offer_id,
            round=self.round,
            from_id=sender,
            to_id=receiver,
            resource=resource,
            amount=checked,
            log_index=len(self.offer_log) - 1,
        )

        edges = self.graph.apply_offer_made(sender, receiver, checked, self.pool.total)

        # The receiver just learned something about the sender's generosity.
        receiver_beliefs = self.beliefs.get(receiver)
        if receiver_beliefs is not None:
            receiver_beliefs.about(sender).observe_their_offer(
                favorability(checked, self.pool.total)
            )

        return [
            OfferMessage(payload=record),
            GraphUpdateMessage(payload=GraphUpdatePayload(edges=edges, reason="offer_made")),
        ]

    def _answer_offer(
        self, *, responder: str, offer_id: str, accepted: bool
    ) -> list[WSMessage]:
        """Resolve a pending offer. Caller must hold the lock."""
        offer = self.pending.get(offer_id)
        if offer is None:
            logger.warning("%s answered unknown offer %r", responder, offer_id)
            return []
        if offer.to_id != responder:
            logger.warning(
                "%s tried to answer offer %r addressed to %s", responder, offer_id, offer.to_id
            )
            return []

        del self.pending[offer_id]

        # The log is append-only in the sense that matters for an audit trail:
        # entries are never removed or reordered. `accepted` is stamped exactly
        # once, when the offer is answered.
        record = self.offer_log[offer.log_index].model_copy(update={"accepted": accepted})
        self.offer_log[offer.log_index] = record

        if accepted:
            self._transfer(offer.from_id, offer.to_id, offer.amount)
            edges = self.graph.apply_offer_accepted(
                offer.from_id, offer.to_id, offer.amount, self.pool.total
            )
            reason = "offer_accepted"
        else:
            edges = self.graph.apply_offer_rejected(offer.from_id, offer.to_id)
            reason = "offer_rejected"

        # The offerer just learned how agreeable the responder is.
        sender_beliefs = self.beliefs.get(offer.from_id)
        if sender_beliefs is not None:
            sender_beliefs.about(responder).observe_response_to_my_offer(accepted)

        return [
            OfferMessage(payload=record),
            GraphUpdateMessage(payload=GraphUpdatePayload(edges=edges, reason=reason)),
        ]

    def _transfer(self, sender: str, receiver: str, amount: float) -> None:
        """Move resource between parties, conserving the pool total.

        Clamped again here because holdings may have fallen between the offer
        being made and being accepted.
        """
        actual = max(0.0, min(amount, self.holdings.get(sender, 0.0)))
        self.holdings[sender] = round(self.holdings.get(sender, 0.0) - actual, 4)
        self.holdings[receiver] = round(self.holdings.get(receiver, 0.0) + actual, 4)

    # ------------------------------------------------------------------ #
    # Human injection
    # ------------------------------------------------------------------ #

    async def inject_offer(self, offer: OfferSchema) -> NegotiationState:
        """Validate a human offer and put it in front of the agents.

        Taking the lock is what makes this safe mid-game: it can only land
        between turns, never halfway through one. The offer becomes pending
        immediately, so the next agent to act sees it in its context.
        """
        if self.finished:
            raise OfferRejected("this session has already finished")

        async with self._lock:
            events = self._record_offer(
                sender=offer.from_,
                receiver=offer.to,
                resource=offer.resource,
                amount=offer.amount,
                strict=True,
            )
            state = self.snapshot()

        for event in events:
            await self._emit(event)
        return state

    # ------------------------------------------------------------------ #
    # Endgame
    # ------------------------------------------------------------------ #

    async def _finish(self) -> None:
        async with self._lock:
            self.finished = True
            self._revealed = revealed_objectives(self._personas)
            self.reveal = RevealPayload(
                revealed_objectives=self._revealed,
                scores=score_all(self.holdings, self.pool.total, self._personas),
                holdings=dict(self.holdings),
                # Built after `_revealed` is set, so the snapshot carries the
                # objectives rather than the pre-reveal `null`.
                final_state=self.snapshot(),
            )
            reveal = self.reveal

        await self._emit(RevealMessage(payload=reveal))

    # ------------------------------------------------------------------ #
    # Emission
    # ------------------------------------------------------------------ #

    async def _emit(self, message: WSMessage) -> None:
        """Push one frame, never letting a broken listener kill the game."""
        try:
            await self._emit_cb(message)
        except Exception:  # pragma: no cover - defensive
            logger.exception("event emitter raised; continuing the negotiation")
