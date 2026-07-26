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

__all__ = ["Action", "ProposedOffer", "OpponentDelta", "AgentDecision"]

Action = Literal["offer", "accept", "reject", "pass"]


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
