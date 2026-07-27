"""Where the live sessions live.

Sessions are keyed by id and fully independent: separate engine, separate trust
graph, separate offer log, separate background round loop. Nothing is shared
between them **except** the LLM client's rate-limit queue, which is global by
design — see the note on `LLMClient._gate` in `app/llm_client.py`.

The engine is reached only through this interface, so the in-memory
implementation can be swapped for Redis or Postgres without the session logic
noticing. Nothing outside this module holds an engine reference across requests.

Two limits keep a small box honest:

**Capacity.** `max_sessions` bounds how many games run at once. The cap exists
because provider quota is per *API key*, not per session — ten simultaneous
negotiations burn the same Gemini budget ten times as fast.

**TTL.** A session nobody has touched for `ttl_seconds` is swept, so an
abandoned browser tab doesn't hold an engine and its round loop forever.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Protocol

from app.engine.negotiation import NegotiationEngine

logger = logging.getLogger("boardroom.session")

__all__ = ["SessionStore", "InMemorySessionStore", "AtCapacity"]


class AtCapacity(RuntimeError):
    """A new session would exceed the concurrent-session cap."""


class SessionStore(Protocol):
    """Storage seam. Async throughout so a networked backend can slot in."""

    async def put(self, engine: NegotiationEngine) -> None: ...

    async def get(self, session_id: str) -> NegotiationEngine | None: ...

    async def remove(self, session_id: str) -> NegotiationEngine | None: ...

    async def active_count(self) -> int: ...

    async def sweep(self) -> list[str]: ...

    async def clear(self) -> None: ...


class InMemorySessionStore:
    """Every live session, keyed by id, in process memory.

    `clock` is injectable so TTL expiry can be tested by fast-forwarding rather
    than by sleeping for the whole TTL in CI.
    """

    def __init__(
        self,
        *,
        max_sessions: int = 5,
        ttl_seconds: float = 600.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._engines: dict[str, NegotiationEngine] = {}
        self._touched: dict[str, float] = {}
        self._max_sessions = max_sessions
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ #
    # Writes
    # ------------------------------------------------------------------ #

    async def put(self, engine: NegotiationEngine) -> None:
        """Register a session, or raise `AtCapacity`.

        Sweeps first: someone arriving at the cap shouldn't be turned away on
        account of sessions that have already aged out.
        """
        await self.sweep()
        async with self._lock:
            if (
                engine.session_id not in self._engines
                and self._live_count() >= self._max_sessions
            ):
                raise AtCapacity(
                    f"{self._live_count()} of {self._max_sessions} sessions in progress"
                )
            self._engines[engine.session_id] = engine
            self._touched[engine.session_id] = self._clock()

    async def remove(self, session_id: str) -> NegotiationEngine | None:
        """Drop a session and stop its round loop."""
        async with self._lock:
            engine = self._engines.pop(session_id, None)
            self._touched.pop(session_id, None)
        if engine is not None:
            await engine.stop()
        return engine

    async def clear(self) -> None:
        async with self._lock:
            engines = list(self._engines.values())
            self._engines.clear()
            self._touched.clear()
        for engine in engines:
            await engine.stop()

    # ------------------------------------------------------------------ #
    # Reads
    # ------------------------------------------------------------------ #

    async def get(self, session_id: str) -> NegotiationEngine | None:
        """Look a session up, marking it as recently used."""
        async with self._lock:
            engine = self._engines.get(session_id)
            if engine is not None:
                self._touched[session_id] = self._clock()
            return engine

    async def active_count(self) -> int:
        """How many sessions currently count against the cap."""
        async with self._lock:
            return self._live_count()

    async def session_ids(self) -> list[str]:
        async with self._lock:
            return list(self._engines)

    def _live_count(self) -> int:
        """Unfinished sessions only. Caller must hold the lock.

        A finished game still occupies memory until the sweep takes it, but it
        makes no provider calls, so holding the next person out on its account
        would be pointless.
        """
        return sum(1 for engine in self._engines.values() if not engine.finished)

    # ------------------------------------------------------------------ #
    # Expiry
    # ------------------------------------------------------------------ #

    async def sweep(self) -> list[str]:
        """Evict sessions untouched for longer than the TTL. Returns their ids."""
        cutoff = self._clock() - self._ttl_seconds
        async with self._lock:
            expired = [
                session_id
                for session_id, touched in self._touched.items()
                if touched < cutoff
            ]
            engines = [
                self._engines.pop(sid) for sid in expired if sid in self._engines
            ]
            for session_id in expired:
                self._touched.pop(session_id, None)

        for engine in engines:
            await engine.stop()
        for session_id in expired:
            logger.info("session %s swept after %.0fs idle", session_id, self._ttl_seconds)
        return expired
