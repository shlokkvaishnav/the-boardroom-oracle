"""Fan out engine events to every connected frontend.

`ConnectionManager.broadcast` matches the engine's `EventEmitter` signature,
so the engine is handed this method directly and never learns what a WebSocket
is. That indirection is what keeps `engine/` testable with a plain list.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import WebSocket

from app.models.messages import WSMessage

logger = logging.getLogger("boardroom.ws")

__all__ = ["ConnectionManager"]


class ConnectionManager:
    """Tracks open sockets and pushes frames to all of them."""

    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    @property
    def connection_count(self) -> int:
        return len(self._connections)

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections.add(websocket)
        logger.info("client connected (%d open)", len(self._connections))

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections.discard(websocket)
        logger.info("client disconnected (%d open)", len(self._connections))

    async def send(self, websocket: WebSocket, message: WSMessage) -> None:
        """Send one frame to one client."""
        await websocket.send_text(message.model_dump_json(by_alias=True))

    async def broadcast(self, message: WSMessage) -> None:
        """Push one frame to every client.

        Serialized once for all recipients. A socket that fails mid-send is
        dropped rather than retried: the game must not stall because one
        browser tab went away.
        """
        async with self._lock:
            targets = list(self._connections)
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
                for websocket in dead:
                    self._connections.discard(websocket)
            logger.info("dropped %d dead connection(s)", len(dead))
