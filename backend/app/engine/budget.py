"""How many provider calls one session may still spend.

The demo runs against a per-minute, per-API-key quota. Every feature that adds
an LLM call — live search today, a claim-linking scribe and a fact-checker next
— spends from the same pocket, so without accounting somewhere the failure mode
is the worst one available: a session that runs out of quota in round four and
never reaches its closing. The discussion just stops, mid-argument, forever.

This module exists so that cannot happen. The rule it enforces:

    **Finishing is not optional. Everything else is.**

Concretely, the budget separates two kinds of spending:

*Floor* — one call per agent per remaining round. This is what it costs to
merely play the session out to its end, and it is always reserved. It is never
spent on anything else, however useful that other thing is.

*Surplus* — whatever is left above the floor. Optional enrichment draws from
here: a web search probe now, a scribe pass or a fact-check later. When the
surplus runs dry the session keeps going, just plainer.

If even the floor becomes unaffordable — a badly misconfigured budget, or a
round that cost far more than predicted — the engine stops early and emits its
closing anyway. A short discussion is a worse demo than a long one. A discussion
with no ending is not a demo at all.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("boardroom.budget")

__all__ = ["CallBudget"]


class CallBudget:
    """A session's provider-call allowance.

    `total=None` means unlimited, which is what a paid key or a mock-agent run
    wants — the accounting still happens, so `spent` is always a truthful
    number to log, but nothing is ever refused.
    """

    def __init__(self, total: int | None = None) -> None:
        self.total = total
        self.spent = 0

    # -- reads -------------------------------------------------------------- #

    @property
    def unlimited(self) -> bool:
        return self.total is None

    @property
    def remaining(self) -> int:
        """Calls left. Effectively infinite when unbounded."""
        if self.total is None:
            return 1_000_000_000
        return max(0, self.total - self.spent)

    def can_afford(self, calls: int) -> bool:
        """Is there room for `calls` more?"""
        return self.unlimited or self.remaining >= calls

    def can_afford_extra(self, *, floor: int, extra: int) -> bool:
        """Is there room for `extra` on top of a reserved `floor`?

        This is the whole point of the type. `floor` is what finishing the
        session costs and is untouchable; `extra` is only granted out of what
        remains above it. Asking this question before every optional call is
        what makes "the session always ends" a structural property rather than
        a hope.
        """
        return self.unlimited or self.remaining >= floor + extra

    # -- writes ------------------------------------------------------------- #

    def spend(self, calls: int) -> None:
        """Record calls already made.

        Spending is recorded after the fact, because an agent turn only reports
        what it used once it is over — a turn that retried on a validation
        failure costs more than one call and cannot say so in advance. Recording
        honestly and possibly overshooting by a call is much safer than
        predicting and quietly under-counting.
        """
        if calls <= 0:
            return
        self.spent += calls
        if not self.unlimited and self.spent > (self.total or 0):
            logger.warning(
                "call budget overspent: %d of %d used", self.spent, self.total
            )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        cap = "unlimited" if self.unlimited else str(self.total)
        return f"<CallBudget spent={self.spent} of {cap}>"
