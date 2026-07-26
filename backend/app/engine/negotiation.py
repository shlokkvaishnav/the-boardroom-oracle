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
    emit closing

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
from app.agents.rapporteur import Rapporteur, RoomSynthesis
from app.agents.scribe import Scribe
from app.config import Settings
from app.engine.budget import CallBudget
from app.engine.chair import next_speaker
from app.engine.knowledge_graph import KnowledgeDelta, KnowledgeGraph
from app.engine.trust_graph import TrustGraph, favorability
from app.models.agent_io import AgentDecision
from app.models.messages import (
    GraphUpdateMessage,
    GraphUpdatePayload,
    KnowledgeUpdateMessage,
    KnowledgeUpdatePayload,
    OfferMessage,
    ClosingMessage,
    ClosingPayload,
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
    KnowledgeNode,
    Pool,
    SearchRecord,
)

logger = logging.getLogger("boardroom.engine")

__all__ = ["EventEmitter", "OfferRejected", "NegotiationEngine"]

#: How the engine talks to the outside world.
EventEmitter = Callable[[WSMessage], Awaitable[None]]

#: How much of the shared log an agent is shown.
RECENT_OFFER_WINDOW = 8

#: How much of the table talk an agent is shown. Roughly the last two rounds
#: with three agents — enough to answer what was just said without the prompt
#: growing without bound over six rounds.
RECENT_REMARK_WINDOW = 8


def _clamp_stance(value: float | None) -> float | None:
    """Confine a self-reported stance to [-1, 1], dropping nonsense.

    `None` passes through untouched — it means "no topic to have a view on",
    which is different from "dead centre" and must stay distinguishable.
    """
    if value is None:
        return None
    if not math.isfinite(value):
        return None
    return round(max(-1.0, min(1.0, float(value))), 3)


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
        scribe: Scribe | None = None,
        rapporteur: Rapporteur | None = None,
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
        #: What gets argued, as opposed to who trusts whom. Shares node ids with
        #: the trust graph, so a party is the same node in both.
        self.knowledge = KnowledgeGraph(
            [(party.id, party.name) for party in self.parties]
        )
        self.beliefs: dict[str, BeliefSet] = {
            agent.id: BeliefSet.for_agent(agent.id, party_ids) for agent in self._agents
        }

        self.round = 0
        self.offer_log: list[OfferRecord] = []
        self.thoughts: list[AgentThought] = []
        self.pending: dict[str, PendingOffer] = {}
        self.finished = False
        self.closing: ClosingPayload | None = None

        self._closing_positions: dict[str, str] | None = None
        self._offer_seq = 0
        #: Provider calls spent in the current round; logged when it ends.
        self._round_llm_calls = 0
        #: The whole session's allowance. Reserves what finishing costs before
        #: letting any optional call spend — see `engine/budget.py`.
        self.budget = CallBudget(settings.session_call_budget or None)
        #: Set when the budget forced an early ending, so the closing can be
        #: read as deliberate rather than as a session that simply stopped.
        self.ended_early = False
        #: Recomputed at the top of every round from the budget.
        self._allow_search = True
        #: Links claims to each other once a round. None when there is no key.
        self._scribe = scribe
        #: Reports where the discussion landed, once, at the very end.
        self._rapporteur = rapporteur
        #: In-flight scribe passes. Held so they can be cancelled on stop and
        #: awaited before the closing, and so they are not garbage-collected
        #: mid-flight — asyncio only keeps a weak reference to a bare task.
        self._scribe_tasks: set[asyncio.Task[None]] = set()
        #: How many claims the scribe has already been shown, so each pass reads
        #: only what is new to it.
        self._claims_read = 0
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
            total_rounds=self.total_rounds,
            pool=self.pool,
            agents=list(self.parties),
            trust_graph=self.graph.view(),
            knowledge_graph=self.knowledge.view(),
            offer_log=list(self.offer_log),
            agent_thoughts=list(self.thoughts),
            holdings=dict(self.holdings),
            closing_positions=self._closing_positions,
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

        # Background passes go first. A reset must not leave a scribe call in
        # flight against a session that no longer exists, still holding a slot
        # in the shared provider queue.
        for scribe_task in list(self._scribe_tasks):
            scribe_task.cancel()
        self._scribe_tasks.clear()

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

                # The floor: one call per agent for every round still to play,
                # this one included. If that is no longer affordable, stop and
                # fall through to `_finish()` — breaking rather than returning
                # is load-bearing, because it is what guarantees a closing frame
                # even on a session cut short.
                floor = len(self._agents) * (self.total_rounds - round_number + 1)
                if not self.budget.can_afford(len(self._agents)):
                    logger.warning(
                        "session %s ending at round %d of %d: call budget spent (%d)",
                        self.session_id,
                        round_number,
                        self.total_rounds,
                        self.budget.spent,
                    )
                    self.ended_early = True
                    break

                # Search is enrichment, so it only ever spends the surplus above
                # the floor. One probe per agent is what a searching round costs.
                self._allow_search = self.budget.can_afford_extra(
                    floor=floor, extra=len(self._agents)
                )

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
                # Reordered within the round, never across it: `waiting` starts
                # as everyone and empties exactly once, so a round still costs
                # one call per agent whoever happens to go first.
                waiting = [agent.id for agent in self._agents]
                by_id = {agent.id: agent for agent in self._agents}
                while waiting:
                    if self._stopped:
                        return
                    speaker = self._pick_speaker(waiting)
                    waiting.remove(speaker)
                    await self._take_turn(by_id[speaker])
                    if self._settings.turn_delay_seconds > 0:
                        await asyncio.sleep(self._settings.turn_delay_seconds)

                # Worth logging because a search-heavy round costs twice the
                # calls of a plain one, and the free tier is a per-minute
                # budget — this is the number to watch in demo rehearsal.
                self._maybe_scribe(floor)

                logger.info(
                    "round %d of session %s used %d provider call(s); %d spent"
                    " of %s this session%s",
                    round_number,
                    self.session_id,
                    self._round_llm_calls,
                    self.budget.spent,
                    "unlimited" if self.budget.unlimited else self.budget.total,
                    "" if self._allow_search else " (search off: no surplus)",
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
            calls = getattr(decision, "llm_calls", 1)
            self._round_llm_calls += calls
            # Recorded after the fact: a turn that retried on a validation
            # failure costs more than one call and can only say so once done.
            self.budget.spend(calls)
            events = self._apply(agent.id, decision)

        for event in events:
            await self._emit(event)

    def _pick_speaker(self, waiting: list[str]) -> str:
        """Who acts next this round. Seating order unless the chair is on.

        Reads engine state without the lock, which is safe because the only
        writer of turn order is this loop. A human remark landing concurrently
        can change `self.thoughts` mid-decision; the worst outcome is that the
        chair uses the remark before or after this pick rather than exactly at
        it, which is not a distinction anyone can observe.
        """
        if not self._settings.enable_chair or len(waiting) == 1:
            return waiting[0]

        last = self.thoughts[-1] if self.thoughts else None
        # Oldest first, so the offer that has waited longest gets answered.
        awaiting = [
            offer.to_id
            for offer in sorted(self.pending.values(), key=lambda o: o.log_index)
        ]
        speaker = next_speaker(
            waiting,
            names={party.id: party.name for party in self.parties},
            last_remark=last.text if last else None,
            last_speaker=last.agent_id if last else None,
            awaiting_answer=awaiting,
        )
        if speaker != waiting[0]:
            logger.info(
                "chair: %s speaks ahead of %s in round %d",
                speaker,
                waiting[0],
                self.round,
            )
        return speaker

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
            recent_remarks=list(self.thoughts[-RECENT_REMARK_WINDOW:]),
            beliefs=self.beliefs.get(agent_id),
            trust_row=self.graph.trust_toward(agent_id, self.graph.party_ids),
            context_topic=self.context_topic,
            allow_search=self._allow_search,
        )

    def _apply(self, agent_id: str, decision: AgentDecision) -> list[WSMessage]:
        """Fold one decision into the state. Caller must hold the lock."""
        events: list[WSMessage] = []

        # `searched` is only present on a TurnDecision — mock agents return a
        # plain AgentDecision — so it's read defensively rather than required.
        searched = list(getattr(decision, "searched", []) or [])
        thought = AgentThought(
            agent_id=agent_id,
            text=decision.thought,
            round=self.round,
            # Clamped here rather than trusted: a model asked for -1..1 will
            # occasionally answer 5, and one wild value would rescale the whole
            # drift chart and make every real movement look flat.
            stance=_clamp_stance(getattr(decision, "stance", None)),
            searched=searched,
        )
        self.thoughts.append(thought)
        events.append(ThoughtMessage(payload=thought))

        knowledge = self._record_claims(agent_id, decision, searched)
        if not knowledge.empty:
            events.append(
                KnowledgeUpdateMessage(
                    payload=KnowledgeUpdatePayload(
                        nodes=knowledge.nodes,
                        edges=knowledge.edges,
                        reason="claim_made",
                    )
                )
            )

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
    # The scribe
    # ------------------------------------------------------------------ #

    def _maybe_scribe(self, floor: int) -> None:
        """Start a linking pass for the round that just ended, if it earned one.

        Returns immediately — the pass runs as a background task. This is the
        whole design constraint: the scribe is an observer, so it must never sit
        between a player and their turn. Called at the end of a round rather
        than awaited anywhere in `_take_turn` for exactly that reason.
        """
        if self._scribe is None or not self._settings.enable_scribe:
            return

        unread = self.knowledge.claim_ids[self._claims_read :]
        if not unread:
            return  # a round where nobody argued costs nothing

        # Enrichment spends surplus only, never the floor that finishing needs.
        if not self.budget.can_afford_extra(floor=floor, extra=1):
            logger.info("skipping scribe pass: no budget surplus")
            return

        # Marked read and paid for up front. Pre-spending avoids a race with the
        # next round's floor check, and a call that fails at the provider has
        # been made regardless — it counts against quota either way.
        self._claims_read = len(self.knowledge.claim_ids)
        self.budget.spend(1)

        # Both lists are snapshotted here rather than inside the task. A
        # backgrounded pass starts whenever the loop next yields, by which time
        # the following round may have added claims — and a pass reporting on
        # round 2 that could see round 3's claims as "earlier" is incoherent.
        # This makes each pass a reading of the table as the round ended.
        fresh = set(unread)
        new_claims = [self.knowledge.node(cid) for cid in unread]
        earlier = [
            self.knowledge.node(cid)
            for cid in self.knowledge.claim_ids
            if cid not in fresh
        ]

        task = asyncio.create_task(
            self._run_scribe(new_claims, earlier),
            name=f"scribe-{self.session_id}-r{self.round}",
        )
        self._scribe_tasks.add(task)
        task.add_done_callback(self._scribe_tasks.discard)

    async def _run_scribe(
        self, new_claims: list[KnowledgeNode], earlier: list[KnowledgeNode]
    ) -> None:
        """One linking pass. Never raises into the loop that spawned it."""
        try:
            # No lock around the call: it is a network round-trip, and holding
            # the lock across it would block human injection for its duration.
            links = await self._scribe.read(  # type: ignore[union-attr]
                new_claims=new_claims, earlier_claims=earlier
            )
            if not links:
                return

            delta = KnowledgeDelta()
            async with self._lock:
                for link in links:
                    # `link_claims` is the authority on what may be recorded: it
                    # refuses anything but supports/contradicts, so a scribe
                    # cannot invent authorship or a citation here.
                    added = self.knowledge.link_claims(
                        source_claim=link.source_claim,
                        target_claim=link.target_claim,
                        kind=link.kind,
                    )
                    delta.nodes.extend(added.nodes)
                    delta.edges.extend(added.edges)

            if delta.empty:
                return

            logger.info(
                "scribe linked %d claim pair(s) in session %s",
                len(delta.edges),
                self.session_id,
            )
            await self._emit(
                KnowledgeUpdateMessage(
                    payload=KnowledgeUpdatePayload(
                        nodes=delta.nodes, edges=delta.edges, reason="scribe"
                    )
                )
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - defensive
            logger.exception("scribe pass failed; the discussion is unaffected")

    async def _settle_scribes(self) -> None:
        """Let outstanding passes finish before the closing snapshot is built.

        Waited on only at the very end, where the game is over and a second or
        two costs nothing anyone is watching for. It is what stops the final
        state from missing a link whose call was still in flight when the last
        round ended.

        Bounded, because the alternative is a session whose ending is hostage to
        a hung provider call — which is precisely the failure the call budget
        exists to prevent, and it would be absurd to reintroduce it here. A pass
        that overruns is cancelled and its links are simply lost.
        """
        pending = [task for task in self._scribe_tasks if not task.done()]
        if not pending:
            return
        timeout = self._settings.scribe_settle_timeout_seconds
        done, still_running = await asyncio.wait(pending, timeout=timeout)
        for task in still_running:
            logger.warning("scribe pass did not settle in %.0fs; cancelling", timeout)
            task.cancel()
        del done

    def _record_claims(
        self, agent_id: str, decision: AgentDecision, searched: list[SearchRecord]
    ) -> KnowledgeDelta:
        """Fold this turn's self-reported claims into the knowledge graph.

        Caller must hold the lock. Costs nothing: the claims arrived inside the
        response the agent was already sending, which is the whole reason they
        are self-reported rather than extracted by a second call.

        Anything the agent looked up this turn is attached as evidence to the
        claims it made in the same breath. That attribution is approximate — the
        agent is not asked which specific hit backs which specific claim, and a
        turn rarely makes more than one substantive point — but the *existence*
        of the search is exact, because the engine stamped it from a tool call
        that really ran. Approximate linkage of real evidence is worth much more
        than precise linkage of evidence a model claimed to have.
        """
        delta = KnowledgeDelta()
        claims = list(getattr(decision, "claims", []) or [])
        if not claims:
            return delta

        for claim in claims:
            text = claim.text.strip()
            if not text:
                continue
            claim_id, added = self.knowledge.add_claim(
                author_id=agent_id,
                text=text,
                claim_kind=claim.kind,
                round=self.round,
                entities=claim.entities,
            )
            delta.nodes.extend(added.nodes)
            delta.edges.extend(added.edges)

            for record in searched:
                cited = self.knowledge.add_evidence(
                    claim_id=claim_id,
                    snippet=record.result_snippet,
                    source_url=record.source_url,
                )
                delta.nodes.extend(cited.nodes)
                delta.edges.extend(cited.edges)

        return delta

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

        self._offer_seq += 1
        offer_id = f"o{self._offer_seq}"

        # The id goes on the record too, so a client reading the offer log can
        # answer a specific pending offer without guessing.
        record = OfferRecord(
            round=self.round,
            from_=sender,
            to=receiver,
            resource=resource,
            amount=checked,
            offer_id=offer_id,
        )
        self.offer_log.append(record)
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

    async def respond_to_offer(
        self, *, responder: str, offer_id: str, accepted: bool
    ) -> NegotiationState:
        """Accept or reject an offer addressed to `responder`.

        The engine has always been able to do this — `_answer_offer` resolves
        the transfer and moves the trust edge — but it was only reachable from
        an agent's own decision, so an offer made *to the human* could never be
        answered and sat pending forever. This is the door.
        """
        async with self._lock:
            offer = self.pending.get(offer_id)
            if offer is None:
                raise OfferRejected(f"no pending offer {offer_id!r}")
            if offer.to_id != responder:
                raise OfferRejected("that offer was not addressed to you")

            events = self._answer_offer(
                responder=responder, offer_id=offer_id, accepted=accepted
            )
            state = self.snapshot()

        for event in events:
            await self._emit(event)
        logger.info(
            "%s %s offer %s", responder, "accepted" if accepted else "rejected", offer_id
        )
        return state

    async def add_remark(self, agent_id: str, text: str) -> AgentThought:
        """Put a human contribution into the discussion.

        The whole point of the table is that people can argue with it, so a
        spoken remark is a first-class turn — it lands in the transcript, is
        broadcast like any other, and appears in every agent's `recent_remarks`
        on their next turn, which is what makes them answer it. It costs no
        round and no offer: you can simply say something.
        """
        cleaned = text.strip()
        if not cleaned:
            raise OfferRejected("nothing was said")

        remark = AgentThought(agent_id=agent_id, text=cleaned, round=self.round)
        async with self._lock:
            self.thoughts.append(remark)
        await self._emit(ThoughtMessage(payload=remark))
        logger.info("%s said: %s", agent_id, cleaned[:80])
        return remark

    async def _finish(self) -> None:
        # Before the snapshot, not after: a link that arrived while the last
        # round was closing belongs in the final state, not only in a delta a
        # client may have missed.
        await self._settle_scribes()

        # Outside the lock, and allowed to fail: the ending itself is not
        # negotiable, so a synthesis that cannot run degrades to the old rule
        # rather than holding up or breaking the closing.
        synthesis = await self._summarise()

        async with self._lock:
            self.finished = True
            self._closing_positions = (
                {s.agent_id: s.position for s in synthesis.statements}
                if synthesis and synthesis.statements
                else self._final_positions()
            )
            self.closing = ClosingPayload(
                positions=self._closing_positions,
                agreed=list(synthesis.agreed) if synthesis else [],
                unresolved=list(synthesis.unresolved) if synthesis else [],
                synthesised=synthesis is not None,
                # Built after the positions are set, so the snapshot carries
                # them rather than the mid-discussion `null`. Final holdings
                # ride along inside it — this payload used to repeat them as a
                # sibling field, which was two sources of truth for one number.
                final_state=self.snapshot(),
            )
            closing = self.closing

        await self._emit(ClosingMessage(payload=closing))

    async def _summarise(self) -> RoomSynthesis | None:
        """Ask the rapporteur where the discussion landed. Never raises.

        Budgeted like every other optional call, with `floor=0` because there
        are no rounds left to reserve for — this runs after the last one. The
        session is over either way; the only question is whether it ends with a
        report or with everyone's last sentence.
        """
        if self._rapporteur is None or not self._settings.enable_synthesis:
            return None
        if not self.budget.can_afford(1):
            logger.info("skipping closing synthesis: no calls left")
            return None

        self.budget.spend(1)
        async with self._lock:
            remarks = list(self.thoughts)
            parties = list(self.parties)
            claims = [self.knowledge.node(cid) for cid in self.knowledge.claim_ids]
            topic = self.context_topic

        try:
            synthesis = await self._rapporteur.summarise(
                topic=topic, parties=parties, remarks=remarks, claims=claims
            )
        except Exception:  # pragma: no cover - defensive
            logger.exception("closing synthesis raised; falling back to last remarks")
            return None

        if synthesis is not None:
            logger.info(
                "session %s closed with %d position(s), %d agreed, %d unresolved",
                self.session_id,
                len(synthesis.statements),
                len(synthesis.agreed),
                len(synthesis.unresolved),
            )
        return synthesis

    def _final_positions(self) -> dict[str, str]:
        """Each party's last statement. The fallback when there is no report.

        This used to be the only ending, for two good reasons: a per-agent
        closing round costs one call each, and a freshly written summary can
        contradict the transcript. The rapporteur answers both — one call for
        the table, and it reads the transcript rather than inventing from it —
        so this is now what happens when the rapporteur cannot run at all.

        It remains deliberately dumb. A last utterance is a poor closing
        statement, but it is always available and never wrong about who said it.
        """
        positions: dict[str, str] = {}
        for remark in self.thoughts:
            positions[remark.agent_id] = remark.text
        return positions

    # ------------------------------------------------------------------ #
    # Emission
    # ------------------------------------------------------------------ #

    async def _emit(self, message: WSMessage) -> None:
        """Push one frame, never letting a broken listener kill the game."""
        try:
            await self._emit_cb(message)
        except Exception:  # pragma: no cover - defensive
            logger.exception("event emitter raised; continuing the negotiation")
