"""The scribe: one pass per round that links claims to each other.

Agents report their own claims, which gets authorship exactly right and costs
nothing. What they cannot do is see *across* the table: only something reading
the whole transcript knows that Rex's "the official forecast is 150,000 tons"
answers Ada's "a 330,000-ton deficit" from earlier in the same round. That
judgement is what this makes.

THREE THINGS IT IS NOT ALLOWED TO DO, each enforced elsewhere and stated here
so the constraint is visible from the code that could violate it:

*Forge provenance.* It returns only `supports` and `contradicts`.
`KnowledgeGraph.link_claims` refuses every other edge kind outright, so even a
scribe that tried to claim someone said something cannot.

*Cost a turn.* It runs once per round, on a small model, as a background task
started after the round's turns are done — never inside one. A link appearing a
few seconds late is invisible; two seconds of dead air before every turn is not.

*Endanger the ending.* Its call comes out of the budget's surplus, never the
floor reserved for finishing. When there is no surplus the round simply gets no
links.
"""

from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.config import Settings
from app.llm_client import LLMClient, LLMError
from app.models.schemas import KnowledgeNode

logger = logging.getLogger("boardroom.scribe")

__all__ = ["ClaimLink", "ScribeReading", "Scribe"]

#: Beyond this many claims the prompt is trimmed to the most recent, oldest
#: dropped first. A six-round session tops out around 36 claims, so this is a
#: guard against a long session rather than a limit that normally bites.
MAX_CLAIMS_IN_PROMPT = 40


class ClaimLink(BaseModel):
    """One relation the scribe believes holds between two claims."""

    model_config = ConfigDict(extra="forbid")

    source_claim: str = Field(description="The id of the claim doing the supporting or contradicting.")
    target_claim: str = Field(description="The id of the earlier claim it bears on.")
    kind: Literal["supports", "contradicts"] = Field(
        description="'contradicts' if both cannot be true, 'supports' if one strengthens the other.",
    )


class ScribeReading(BaseModel):
    """Everything the scribe found in one round. Frequently empty."""

    model_config = ConfigDict(extra="forbid")

    links: list[ClaimLink] = Field(
        default_factory=list,
        description="Relations between claims. Empty when nothing clearly relates.",
    )


SYSTEM_PROMPT = "\n".join(
    [
        "You read a transcript of claims made in a negotiation and report only how "
        "they relate to each other. You take no side and make no claims of your own.",
        "",
        "You are looking for two things:",
        "  - CONTRADICTS: the two claims cannot both be true. Different numbers for "
        "    the same quantity, opposite predictions about the same thing, a denial "
        "    of something asserted.",
        "  - SUPPORTS: one claim gives a concrete reason to believe the other.",
        "",
        "BE STRICT. Most pairs of claims are simply about different things, and "
        "reporting those as related is worse than reporting nothing — it puts a "
        "line on a graph that a reader will trust. Specifically, none of these is "
        "a relation:",
        "  - two claims merely mentioning the same subject",
        "  - a value judgement agreeing in spirit with a factual claim",
        "  - two parties wanting different outcomes (that is the negotiation, not "
        "    a contradiction)",
        "  - a claim restating another in different words",
        "",
        "Returning an empty list is the correct answer most rounds. Never link a "
        "claim to itself, and only use ids that appear in the list you are given.",
    ]
)


class Scribe:
    """Reads a round and reports how its claims bear on each other."""

    def __init__(self, llm: LLMClient, settings: Settings) -> None:
        self._llm = llm
        self._settings = settings
        #: Consecutive failed passes. Exists because of a real incident: the
        #: configured model name was one that does not exist, every call 404'd,
        #: each failure logged a tidy warning and returned no links — and the
        #: feature was indistinguishable from a scribe finding nothing to link.
        #: A broken thing must not look like a quiet thing.
        self._consecutive_failures = 0

    def _note_failure(self, exc: Exception) -> None:
        """Log a failed pass, escalating once it stops looking like bad luck.

        One failure is a flaky call. Three in a row is a misconfiguration, and
        it is worth saying so in the terms most likely to be true — because the
        symptom a person actually sees is a graph with no links in it, which
        points nowhere near the model name.
        """
        self._consecutive_failures += 1
        if self._consecutive_failures < 3:
            logger.warning(
                "scribe pass failed (%d in a row), round gets no links: %s",
                self._consecutive_failures,
                exc,
            )
            return
        logger.error(
            "scribe has failed %d passes in a row and is producing no links at "
            "all — check SCRIBE_MODEL=%r is a model this key can reach. Last "
            "error: %s",
            self._consecutive_failures,
            self._settings.scribe_model,
            exc,
        )

    @staticmethod
    def _render(claims: list[KnowledgeNode]) -> str:
        return "\n".join(
            f"  [{claim.id}] ({claim.author_id}, round {claim.round}, "
            f"{claim.claim_kind}) {claim.label}"
            for claim in claims
        )

    async def read(
        self, *, new_claims: list[KnowledgeNode], earlier_claims: list[KnowledgeNode]
    ) -> list[ClaimLink]:
        """Find relations involving this round's claims.

        Never raises. Every failure path returns no links, because a round
        without links is a slightly poorer graph and a raised exception in a
        background task is a session that dies for a cosmetic feature.
        """
        if not new_claims:
            return []

        earlier = earlier_claims[-MAX_CLAIMS_IN_PROMPT:]
        prompt = "\n".join(
            [
                "CLAIMS MADE EARLIER:",
                self._render(earlier) or "  (none — this is the first round with claims)",
                "",
                "CLAIMS JUST MADE, which are the ones you are reporting on:",
                self._render(new_claims),
                "",
                "For each of the claims just made, report any claim above that it "
                "contradicts or supports. A claim just made may also relate to "
                "another claim just made. Report nothing you are not confident in.",
            ]
        )

        try:
            raw = await self._llm.generate_structured(
                prompt,
                ScribeReading,
                system=SYSTEM_PROMPT,
                model=self._settings.scribe_model or None,
            )
            reading = ScribeReading.model_validate(raw)
        except (LLMError, ValueError) as exc:
            # Includes ValidationError, which subclasses ValueError.
            self._note_failure(exc)
            return []

        self._consecutive_failures = 0

        valid_ids = {claim.id for claim in earlier} | {claim.id for claim in new_claims}
        links = [
            link
            for link in reading.links
            if link.source_claim in valid_ids
            and link.target_claim in valid_ids
            and link.source_claim != link.target_claim
        ]
        dropped = len(reading.links) - len(links)
        if dropped:
            logger.info("scribe proposed %d link(s) naming unknown claims; dropped", dropped)
        return links
