"""Turn a free-form spoken sentence into a structured offer.

One Claude call with a JSON Schema, the same retry-once-then-give-up ladder as
the agents, and a hard validation pass afterwards. "Give up" here means
returning `None` rather than a safe default: an offer the system isn't sure
about must never be queued silently — the endpoint hands the transcript and a
`null` back so a person can decide.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from anthropic import AsyncAnthropic
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config import Settings
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


DRAFT_SCHEMA: dict[str, Any] = VoiceOfferDraft.model_json_schema()


class VoiceOfferParser:
    """Free-form transcript -> validated `OfferSchema`, or `None`."""

    def __init__(self, client: AsyncAnthropic, settings: Settings) -> None:
        self._client = client
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
                "",
                "Return only a JSON object matching the schema.",
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

        messages: list[dict[str, Any]] = [
            {"role": "user", "content": f"Spoken sentence:\n{transcript}"}
        ]

        for attempt in (1, 2):
            raw = ""
            try:
                raw = await self._request(
                    messages, self.system_prompt(parties, resource, speaker_id)
                )
                draft = VoiceOfferDraft.model_validate_json(raw)
                return self._to_offer(draft, parties, resource, speaker_id)
            except (ValidationError, json.JSONDecodeError, ValueError) as exc:
                logger.warning("voice parse attempt %d failed: %s", attempt, exc)
                if attempt == 1:
                    messages = [
                        *messages,
                        {"role": "assistant", "content": raw or "(empty response)"},
                        {
                            "role": "user",
                            "content": (
                                f"That was rejected: {exc}\n"
                                "Reply again with only a JSON object matching the schema."
                            ),
                        },
                    ]
            except Exception as exc:
                logger.warning("voice parse call failed on attempt %d: %s", attempt, exc)

        return None

    async def _request(self, messages: list[dict[str, Any]], system: str) -> str:
        response = await self._client.messages.create(
            model=self._settings.anthropic_model,
            max_tokens=512,
            system=system,
            messages=messages,  # type: ignore[arg-type]
            output_config={
                "format": {"type": "json_schema", "schema": DRAFT_SCHEMA},
                "effort": "low",
            },
        )
        if getattr(response, "stop_reason", None) == "refusal":
            raise ValueError("model declined to parse this transcript")
        for block in response.content:
            if getattr(block, "type", None) == "text":
                return block.text
        raise ValueError("response contained no text block")

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
