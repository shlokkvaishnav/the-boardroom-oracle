"""Where the live session lives.

The demo needs exactly one concurrent session, but the engine is reached only
through this interface so the in-memory implementation can be swapped for
Redis or Postgres without the game logic noticing. Nothing outside this module
holds an engine reference across requests.
"""

from __future__ import annotations

import asyncio
from typing import Protocol

from app.engine.negotiation import NegotiationEngine

__all__ = ["SessionStore", "InMemorySessionStore"]


class SessionStore(Protocol):
    """Storage seam. Async throughout so a networked backend can slot in."""

    async def put(self, engine: NegotiationEngine) -> None: ...

    async def current(self) -> NegotiationEngine | None: ...

    async def get(self, session_id: str) -> NegotiationEngine | None: ...

    async def clear(self) -> None: ...


class InMemorySessionStore:
    """Holds the one active session in process memory.

    Replacing the previous session tears it down first, so a stray background
    round loop can never outlive the session it belongs to.
    """

    def __init__(self) -> None:
        self._engine: NegotiationEngine | None = None
        self._lock = asyncio.Lock()

    async def put(self, engine: NegotiationEngine) -> None:
        async with self._lock:
            previous, self._engine = self._engine, engine
        if previous is not None:
            await previous.stop()

    async def current(self) -> NegotiationEngine | None:
        async with self._lock:
            return self._engine

    async def get(self, session_id: str) -> NegotiationEngine | None:
        async with self._lock:
            engine = self._engine
        if engine is not None and engine.session_id == session_id:
            return engine
        return None

    async def clear(self) -> None:
        async with self._lock:
            engine, self._engine = self._engine, None
        if engine is not None:
            await engine.stop()
