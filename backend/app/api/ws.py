"""The live feed: `WS /ws/negotiation`.

On connect the client is sent one `state` frame carrying the full
`NegotiationState`, so it can render immediately without a REST round-trip and
without having missed anything. Everything after that is incremental frames
pushed by the engine.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.models.messages import StateMessage

logger = logging.getLogger("boardroom.ws")

router = APIRouter(tags=["websocket"])

#: Close code for an unknown or expired session id. 1008 is "policy violation",
#: the closest standard code for "that id isn't something I'll serve".
WS_UNKNOWN_SESSION = 1008


@router.websocket("/ws/negotiation/{session_id}")
async def negotiation_feed(websocket: WebSocket, session_id: str) -> None:
    manager = websocket.app.state.manager
    store = websocket.app.state.store

    engine = await store.get(session_id)
    if engine is None:
        # Closed rather than fed an empty state forever: an unknown or expired
        # id is not a session that might start later, and a client that can see
        # the difference can tell the user to start a new one.
        await websocket.accept()
        await websocket.close(
            code=WS_UNKNOWN_SESSION, reason=f"unknown session {session_id}"
        )
        logger.info("rejected websocket for unknown session %s", session_id)
        return

    await manager.connect(session_id, websocket)
    try:
        await manager.send(websocket, StateMessage(payload=engine.snapshot()))

        # The engine pushes; this loop only keeps the socket open and notices
        # when the client goes away.
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("websocket closed unexpectedly")
    finally:
        await manager.disconnect(session_id, websocket)
