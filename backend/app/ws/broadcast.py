"""Fan out engine events to every connected frontend.

`ConnectionManager.broadcast` matches the engine's `EventEmitter` signature,
so the engine is handed this method directly and never learns what a WebSocket
is. That indirection is what keeps `engine/` testable with a plain list.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from fastapi import WebSocket

from app.models.messages import WSMessage

logger = logging.getLogger("boardroom.ws")

__all__ = ["ConnectionManager"]


class ConnectionManager:
    """Tracks open sockets per session and pushes frames to the right ones.

    Sockets are grouped by session id: a frame from one negotiation must never
    reach a viewer of another. `emitter_for(session_id)` returns a callable
    matching the engine's `EventEmitter` signature, so the engine still knows
    nothing about sessions-as-routing or about WebSockets at all.
    """

    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = {}
        self._lock = asyncio.Lock()

    @property
    def connection_count(self) -> int:
        return sum(len(sockets) for sockets in self._connections.values())

    def session_connection_count(self, session_id: str) -> int:
        return len(self._connections.get(session_id, ()))

    async def connect(self, session_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections.setdefault(session_id, set()).add(websocket)
        logger.info(
            "client connected to session %s (%d open on it, %d total)",
            session_id,
            self.session_connection_count(session_id),
            self.connection_count,
        )

    async def disconnect(self, session_id: str, websocket: WebSocket) -> None:
        async with self._lock:
            sockets = self._connections.get(session_id)
            if sockets is not None:
                sockets.discard(websocket)
                if not sockets:
                    del self._connections[session_id]
        logger.info("client disconnected from session %s", session_id)

    async def send(self, websocket: WebSocket, message: WSMessage) -> None:
        """Send one frame to one client."""
        await websocket.send_text(message.model_dump_json(by_alias=True))

    def emitter_for(self, session_id: str) -> Callable[[WSMessage], Awaitable[None]]:
        """An `EventEmitter` bound to one session, for that session's engine."""

        async def emit(message: WSMessage) -> None:
            await self.broadcast(session_id, message)

        return emit

    async def broadcast(self, session_id: str, message: WSMessage) -> None:
        """Push one frame to every client watching `session_id`.

        Serialized once for all recipients. A socket that fails mid-send is
        dropped rather than retried: the session must not stall because one
        browser tab went away.
        """
        async with self._lock:
            targets = list(self._connections.get(session_id, ()))
        if not targets:
            return

        payload = message.model_dump_json(by_alias=True)
        dead: list[WebSocket] = []
        for websocket in targets:
            try:
                await websocket.send_text(payload)
            except Exception:
                dead.append(websocket)

        if dead:
            async with self._lock:
                sockets = self._connections.get(session_id)
                if sockets is not None:
                    for websocket in dead:
                        sockets.discard(websocket)
                    if not sockets:
                        del self._connections[session_id]
            logger.info(
                "dropped %d dead connection(s) on session %s", len(dead), session_id
            )
