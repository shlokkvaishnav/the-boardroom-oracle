"""The structured JSON contract for a single agent turn.

This is what every agent — LLM or mock — must return. Gemini's JSON mode is
handed this schema directly (`response_schema`), so the model is constrained
rather than asked nicely; the response is then validated against it anyway, and
anything that fails goes down the retry-then-safe-default path in
`agents/llm_agent.py`.

These models are internal (agent <-> engine), not part of the frontend
contract, so they deliberately do *not* inherit from `ContractModel`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.schemas import SearchRecord

__all__ = [
    "Action",
    "ClaimKind",
    "Claim",
    "ProposedOffer",
    "OpponentDelta",
    "AgentDecision",
    "TurnDecision",
]

Action = Literal["offer", "accept", "reject", "pass"]

#: What kind of thing a claim is, which decides what can be done with it.
#:
#: The distinction earns its place by telling the fact-checker what is even
#: checkable: "copper output fell 12% last year" can be verified against a
#: source, "we should protect the smaller supplier first" cannot, and spending a
#: search on the second is a wasted call and a category error. Asking the
#: speaker to label its own claim is far cheaper and more accurate than
#: inferring it downstream.
ClaimKind = Literal["fact", "value", "prediction"]


class Claim(BaseModel):
    """One assertion an agent is making, stated structurally.

    This is the cheap half of the knowledge graph. The agent is already
    composing a `thought`; naming what it is claiming *while* it says it costs
    no extra provider call and gets authorship exactly right, because the
    speaker reported it rather than an observer guessing.

    What it deliberately does not carry is how this claim relates to anyone
    else's. An agent cannot reliably see that its point contradicts something
    said two turns ago by someone else — that is a cross-transcript judgement,
    and it belongs to the scribe pass that reads the whole round at once.
    """

    model_config = ConfigDict(extra="forbid")

    text: str = Field(
        description=(
            "The claim itself, as one plain sentence that stands on its own. Not a "
            "quote of what you said — the point underneath it."
        ),
    )
    kind: ClaimKind = Field(
        description=(
            "'fact' if it could be checked against a source, 'prediction' if it is "
            "about what will happen, 'value' if it is a judgement about what matters."
        ),
    )
    entities: list[str] = Field(
        default_factory=list,
        description=(
            "The few concrete things this claim is about — a place, an organisation, "
            "a number, a date. Plain names, no articles. May be empty."
        ),
    )


class ProposedOffer(BaseModel):
    """An offer an agent wants to make this turn."""

    model_config = ConfigDict(extra="forbid")

    to: str = Field(description="The id of the party this offer is directed at.")
    resource: str = Field(description="The resource being offered.")
    amount: float = Field(description="How much of the resource to transfer to `to`.")


class OpponentDelta(BaseModel):
    """How this turn changed the agent's belief about one other party."""

    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(description="The party this belief update is about.")
    trust_delta: float = Field(
        description="Change in trust toward that party, between -1.0 and 1.0.",
    )
    note: str | None = Field(
        default=None,
        description="Optional one-clause reason for the change.",
    )


class AgentDecision(BaseModel):
    """One agent's complete turn."""

    model_config = ConfigDict(extra="forbid")

    action: Action = Field(
        description=(
            "'offer' to propose a new transfer, 'accept' or 'reject' to respond to a "
            "pending offer directed at you, or 'pass' to do nothing this turn."
        ),
    )
    offer: ProposedOffer | None = Field(
        default=None,
        description="The proposed offer. Required when action is 'offer', otherwise null.",
    )
    target_offer_id: str | None = Field(
        default=None,
        description=(
            "The id of the pending offer being responded to. Required when action is "
            "'accept' or 'reject', otherwise null."
        ),
    )
    thought: str = Field(
        description="One short first-person line explaining the move, shown live to the audience.",
    )
    opponent_updates: list[OpponentDelta] = Field(
        default_factory=list,
        description="Belief updates about other parties. May be empty.",
    )
    stance: float | None = Field(
        default=None,
        description=(
            "Where you now stand on the matter under discussion, from -1.0 "
            "(completely against it) through 0.0 (genuinely undecided) to 1.0 "
            "(completely for it). Report where you actually are this turn, not "
            "where you started: if someone made a point that moved you, the "
            "number should move. Null when there is no matter on the table."
        ),
    )
    claims: list[Claim] = Field(
        default_factory=list,
        description=(
            "The substantive assertions inside what you just said, at most two. "
            "Only points about the matter under discussion — never claims about the "
            "negotiation itself ('Rex is being difficult'). Empty if you only "
            "agreed, asked something, or made a move without arguing for it."
        ),
    )

    @model_validator(mode="after")
    def _check_action_payload(self) -> AgentDecision:
        """An action must carry the payload it needs, and no other.

        A violation here is what triggers the retry-with-error path, so the
        messages are written to be useful when fed back to the model.
        """
        if self.action == "offer":
            if self.offer is None:
                raise ValueError("action 'offer' requires a non-null `offer` object")
            if self.target_offer_id is not None:
                raise ValueError("action 'offer' must not set `target_offer_id`")
        elif self.action in ("accept", "reject"):
            if not self.target_offer_id:
                raise ValueError(
                    f"action '{self.action}' requires `target_offer_id` naming a pending offer"
                )
            if self.offer is not None:
                raise ValueError(f"action '{self.action}' must not set `offer`")
        else:  # pass
            if self.offer is not None or self.target_offer_id is not None:
                raise ValueError("action 'pass' must set neither `offer` nor `target_offer_id`")
        return self

    @classmethod
    def safe_default(cls, reason: str = "malformed response") -> AgentDecision:
        """The fallback used when an agent can't produce a valid decision.

        Passing is the only action that is always legal and never mutates the
        pool, so a broken turn degrades to a visible no-op rather than an
        invented offer.
        """
        return cls(action="pass", thought=f"(no move this turn — {reason})")


class TurnDecision(AgentDecision):
    """A decision plus how it was arrived at. Never sent to a model.

    `AgentDecision` is handed to Gemini as `response_schema`, so anything
    declared on it becomes a field the model is asked to invent. Provenance
    therefore lives on this subclass instead, stamped server-side from what
    actually happened. Being a subclass keeps it a valid `AgentDecision`
    everywhere the engine and the mock agents already handle one.
    """

    #: Non-empty only when the agent really did invoke `web_search` this turn.
    searched: list[SearchRecord] = Field(default_factory=list)
    #: Provider calls this turn cost — 1 normally, 2 when the tool was offered.
    #: Summed per round so a search-heavy round's latency is visible in the log.
    llm_calls: int = 1

    @classmethod
    def of(
        cls,
        decision: AgentDecision,
        *,
        searched: list[SearchRecord] | None = None,
        llm_calls: int = 1,
    ) -> TurnDecision:
        """Wrap a validated decision, preserving it exactly.

        Rebuilt from a dump rather than mutated so `_sanitize`'s repairs — which
        may legitimately return a plain `AgentDecision` — are carried across.
        """
        return cls.model_validate(
            {
                **decision.model_dump(),
                "searched": [record.model_dump() for record in searched or []],
                "llm_calls": llm_calls,
            }
        )
