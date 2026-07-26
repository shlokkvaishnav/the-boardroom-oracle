"""The rapporteur: what the room actually concluded.

Sessions used to end by taking each party's *last utterance* as their closing
position. That was chosen deliberately — a per-agent closing round costs one
call each against a shared rate limit, and a freshly written summary can
contradict what someone spent the discussion actually saying. Both objections
were right. The result was still wrong: a last utterance is usually a sentence
from the middle of an argument, so the session stopped rather than concluded.

This answers both objections instead of accepting them.

*On cost:* one call for the whole table, not one per agent, and only at the
very end.

*On contradiction:* the rapporteur is given the transcript and the claims each
party actually made, and is told to report positions rather than invent them.
It is a reader, not a participant — it takes no side and adds no argument.

And it degrades safely. If the call fails, or the budget has nothing spare, the
closing still happens using the old last-utterance rule. Ending the session is
never optional; ending it *well* is the enrichment.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, ConfigDict, Field

from app.config import Settings
from app.llm_client import LLMClient, LLMError
from app.models.schemas import AgentInfo, AgentThought, KnowledgeNode

logger = logging.getLogger("boardroom.rapporteur")

__all__ = ["ClosingStatement", "RoomSynthesis", "Rapporteur"]

#: How much of the transcript the rapporteur reads. A six-round session with
#: three agents is 18 remarks, so this normally takes everything.
MAX_TRANSCRIPT = 40


class ClosingStatement(BaseModel):
    """Where one party finished standing."""

    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(description="The id of the party, exactly as given.")
    position: str = Field(
        description=(
            "Their settled position on the matter, in one or two sentences, in "
            "their own terms. Report what they argued — do not improve it, "
            "soften it, or make them agree with anyone."
        ),
    )


class RoomSynthesis(BaseModel):
    """What the table concluded, as a whole."""

    model_config = ConfigDict(extra="forbid")

    statements: list[ClosingStatement] = Field(
        default_factory=list,
        description="One per party that spoke.",
    )
    agreed: list[str] = Field(
        default_factory=list,
        description=(
            "Points the room genuinely converged on, each in one short line. "
            "Empty if they agreed on nothing — which is a real outcome, not a "
            "failure to summarise."
        ),
    )
    unresolved: list[str] = Field(
        default_factory=list,
        description="Points still in dispute when time ran out, one short line each.",
    )


SYSTEM_PROMPT = "\n".join(
    [
        "You are the rapporteur for a discussion that has just ended. You report "
        "what was said and where it landed. You take no side, add no argument of "
        "your own, and introduce no fact that nobody raised.",
        "",
        "Two failures to avoid, both of which make the report useless:",
        "  - Manufacturing consensus. If the room did not converge, `agreed` is "
        "    empty. A discussion that ended in disagreement is a real outcome and "
        "    reporting it honestly is the job.",
        "  - Flattening people. Each party argued a particular line, often a "
        "    sharp one. Report it as they meant it, in their register, not as a "
        "    diplomatic paraphrase that could have come from any of them.",
        "",
        "Use only the party ids you are given. Be brief: one or two sentences per "
        "position, one short line per point.",
    ]
)


class Rapporteur:
    """Reads a finished discussion and reports where it landed."""

    def __init__(self, llm: LLMClient, settings: Settings) -> None:
        self._llm = llm
        self._settings = settings

    @staticmethod
    def _render_transcript(remarks: list[AgentThought], names: dict[str, str]) -> str:
        return "\n".join(
            f"  {names.get(remark.agent_id, remark.agent_id)}: {remark.text}"
            for remark in remarks[-MAX_TRANSCRIPT:]
        )

    @staticmethod
    def _render_claims(claims: list[KnowledgeNode], names: dict[str, str]) -> str:
        if not claims:
            return "  (nobody stated a claim outright)"
        return "\n".join(
            f"  {names.get(claim.author_id or '', claim.author_id)} "
            f"({claim.claim_kind}): {claim.label}"
            for claim in claims
        )

    async def summarise(
        self,
        *,
        topic: str | None,
        parties: list[AgentInfo],
        remarks: list[AgentThought],
        claims: list[KnowledgeNode],
    ) -> RoomSynthesis | None:
        """Report where the discussion landed, or `None` if it could not.

        `None` rather than a raised exception or an empty synthesis: the caller
        needs to tell "no report" from "a report saying they agreed on nothing",
        because the first falls back to the old rule and the second is a result.
        """
        if not remarks:
            return None

        names = {party.id: party.name for party in parties}
        roster = "\n".join(
            f"  - {party.id} ({party.name}, {party.persona})" for party in parties
        )
        prompt = "\n".join(
            [
                f"THE MATTER DISCUSSED: {topic}" if topic else "No set topic.",
                "",
                "PARTIES (use these ids):",
                roster,
                "",
                "WHAT WAS SAID, in order:",
                self._render_transcript(remarks, names),
                "",
                "CLAIMS EACH PARTY STATED OUTRIGHT:",
                self._render_claims(claims, names),
                "",
                "Report each party's settled position, what the room agreed on, and "
                "what it did not.",
            ]
        )

        try:
            raw = await self._llm.generate_structured(prompt, RoomSynthesis, system=SYSTEM_PROMPT)
            synthesis = RoomSynthesis.model_validate(raw)
        except (LLMError, ValueError) as exc:
            logger.warning("closing synthesis failed, falling back to last remarks: %s", exc)
            return None

        # A statement attributed to a party that was never at the table is the
        # one failure worth dropping outright — it would put invented words in
        # a named mouth on the final screen.
        known = {party.id for party in parties}
        kept = [s for s in synthesis.statements if s.agent_id in known]
        if len(kept) != len(synthesis.statements):
            logger.warning(
                "rapporteur named %d unknown part(y/ies); dropped",
                len(synthesis.statements) - len(kept),
            )
        return synthesis.model_copy(update={"statements": kept})
