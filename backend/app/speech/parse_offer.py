"""Turn a free-form spoken sentence into a structured offer.

One Gemini call in JSON mode, one retry, then a hard validation pass against
the live table. "Give up" here means returning `None` rather than a safe
default: an offer the system isn't sure about must never be queued silently —
the endpoint hands the transcript and a `null` back so a person can decide.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config import Settings
from app.llm_client import LLMClient, LLMError
from app.models.schemas import AgentInfo, OfferSchema

logger = logging.getLogger("boardroom.speech")

__all__ = ["VoiceOfferDraft", "VoiceOfferParser"]


class VoiceOfferDraft(BaseModel):
    """What the model extracts, before it's checked against the live table."""

    model_config = ConfigDict(extra="forbid")

    understood: bool = Field(
        description=(
            "True only if the speaker clearly named both a recipient and an amount. "
            "False for greetings, questions, or anything ambiguous."
        )
    )
    to: str | None = Field(
        default=None,
        description="The id of the party being offered resource. Must be one of the listed ids.",
    )
    amount: float | None = Field(
        default=None,
        description="How much resource to transfer. Numbers spoken as words must be converted.",
    )


class VoiceOfferParser:
    """Free-form transcript -> validated `OfferSchema`, or `None`."""

    def __init__(self, llm: LLMClient, settings: Settings) -> None:
        self._llm = llm
        self._settings = settings

    def system_prompt(self, parties: list[AgentInfo], resource: str, speaker_id: str) -> str:
        roster = "\n".join(
            f"  - {party.id}: {party.name}, the {party.persona}" for party in parties
        )
        return "\n".join(
            [
                "You convert a spoken sentence into a structured negotiation offer.",
                "",
                f"The speaker is {speaker_id}. An offer transfers {resource} FROM the "
                f"speaker TO another party, so `to` must never be {speaker_id!r}.",
                "",
                "Parties at the table (use these exact ids):",
                roster,
                "",
                "Rules:",
                "  - Speakers refer to parties by name, by role, or loosely "
                "    ('the aggressive one'). Map to the closest id.",
                "  - Convert spoken numbers to digits: 'twelve and a half' -> 12.5.",
                "  - Set understood=false if no clear recipient or no clear amount was "
                "    given, or if the sentence is a question rather than an offer.",
                "  - Never guess an amount that wasn't said.",
            ]
        )

    async def parse(
        self,
        transcript: str,
        *,
        parties: list[AgentInfo],
        resource: str,
        speaker_id: str,
    ) -> OfferSchema | None:
        if not transcript.strip():
            return None

        system = self.system_prompt(parties, resource, speaker_id)
        prompt = f"Spoken sentence:\n{transcript}"

        for attempt in (1, 2):
            try:
                raw = await self._llm.generate_structured(
                    prompt, VoiceOfferDraft, system=system
                )
                draft = VoiceOfferDraft.model_validate(raw)
                return self._to_offer(draft, parties, resource, speaker_id)

            except ValidationError as exc:
                logger.warning("voice parse attempt %d failed validation: %s", attempt, exc)
                if attempt == 1:
                    prompt = (
                        f"Spoken sentence:\n{transcript}\n\n"
                        f"---\nYour previous reply was rejected: {exc}\n"
                        "Reply again with a corrected JSON object."
                    )

            except LLMError as exc:
                logger.warning("voice parse call failed on attempt %d: %s", attempt, exc)

        return None

    @staticmethod
    def _to_offer(
        draft: VoiceOfferDraft,
        parties: list[AgentInfo],
        resource: str,
        speaker_id: str,
    ) -> OfferSchema | None:
        """Check the draft against the real table. Anything off returns None."""
        if not draft.understood:
            return None
        if draft.to is None or draft.amount is None:
            return None

        valid_ids = {party.id for party in parties}
        if draft.to not in valid_ids or draft.to == speaker_id:
            logger.info("voice parse produced an unusable recipient %r", draft.to)
            return None
        if draft.amount <= 0:
            return None

        return OfferSchema(
            from_=speaker_id,
            to=draft.to,
            resource=resource,
            amount=round(float(draft.amount), 4),
        )
