"""Who speaks next.

Turn order used to be fixed seating order, every round, forever. That is why
the discussion sometimes reads as three monologues: an agent gets challenged by
name and then two other people talk before it can answer, by which point the
conversation has moved and the challenge is stale.

The chair fixes that with a rule rather than a model. No provider call is made
here — deciding who answers a question is not a task that needs a language
model, it needs the observation that being named is a strong signal you were
just spoken to.

THE RULE, in order:

1. **Answer the person who addressed you.** If the last thing said named
   exactly one party who has not yet spoken this round, they go next.
2. **Answer what is on the table.** Otherwise, whoever is holding the oldest
   unanswered offer goes next — they have been asked a question in the only
   other way this table has of asking one.
3. **Otherwise, seating order.** The old behaviour, unchanged.

FAIRNESS IS NOT NEGOTIABLE
    The chair only ever reorders *within* a round. Everyone still acts exactly
    once per round, because an agent that could be starved of turns by never
    being mentioned would slowly drop out of a discussion it is supposed to be
    part of — and the personas are chosen so that the quiet one is often the
    one worth hearing from.

    This is also what keeps the call budget's floor arithmetic honest: rounds
    still cost exactly one call per agent, whatever order they happen in.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence

logger = logging.getLogger("boardroom.chair")

__all__ = ["mentioned_parties", "next_speaker"]


def mentioned_parties(text: str, names: Mapping[str, str]) -> list[str]:
    """Party ids named in `text`, in the order they first appear.

    Matches on display name or id, at word boundaries so "Mira" does not fire
    on "Miranda" and "Rex" does not fire on "Rexall". Deliberately literal: a
    party is either named or it is not, and guessing at oblique references
    ("the cooperator over there") would make turn order unpredictable for no
    real gain.
    """
    found: list[tuple[int, str]] = []
    for party_id, name in names.items():
        earliest: int | None = None
        for needle in {name, party_id}:
            if not needle:
                continue
            match = re.search(rf"\b{re.escape(needle)}\b", text, re.IGNORECASE)
            if match and (earliest is None or match.start() < earliest):
                earliest = match.start()
        if earliest is not None:
            found.append((earliest, party_id))
    return [party_id for _, party_id in sorted(found)]


def next_speaker(
    waiting: Sequence[str],
    *,
    names: Mapping[str, str],
    last_remark: str | None = None,
    last_speaker: str | None = None,
    awaiting_answer: Sequence[str] = (),
) -> str:
    """Choose who acts next from `waiting`, which is in seating order.

    `waiting` must be non-empty. `awaiting_answer` is party ids holding an
    unanswered offer, oldest first.
    """
    if not waiting:
        raise ValueError("next_speaker called with nobody waiting")
    eligible = set(waiting)

    # 1. Named by whoever just spoke.
    if last_remark:
        for party_id in mentioned_parties(last_remark, names):
            # Somebody naming themselves is not an invitation to speak again.
            if party_id in eligible and party_id != last_speaker:
                logger.debug("chair: %s was named, so answers next", party_id)
                return party_id

    # 2. Holding an unanswered offer.
    for party_id in awaiting_answer:
        if party_id in eligible:
            logger.debug("chair: %s has an offer to answer", party_id)
            return party_id

    # 3. Seating order.
    return waiting[0]
