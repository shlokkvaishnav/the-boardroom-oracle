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
from app.models.schemas import NegotiationState, Pool

logger = logging.getLogger("boardroom.ws")

router = APIRouter(tags=["websocket"])


@router.websocket("/ws/negotiation")
async def negotiation_feed(websocket: WebSocket) -> None:
    manager = websocket.app.state.manager
    store = websocket.app.state.store
    settings = websocket.app.state.settings

    await manager.connect(websocket)
    try:
        engine = await store.current()
        if engine is not None:
            state = engine.snapshot()
        else:
            # No session yet: send an empty but well-formed state so the client
            # renders a table rather than waiting on a frame that never comes.
            state = NegotiationState(
                round=0,
                pool=Pool(resource=settings.pool_resource, total=settings.pool_total),
            )
        await manager.send(websocket, StateMessage(payload=state))

        # The engine pushes; this loop only keeps the socket open and notices
        # when the client goes away.
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("websocket closed unexpectedly")
    finally:
        await manager.disconnect(websocket)
