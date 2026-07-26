"""Milestone 7: the WebSocket broadcast layer."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.api.ws import WS_UNKNOWN_SESSION

from app.config import Settings
from app.main import create_app
from app.models.messages import RoundChangeMessage, RoundChangePayload
from app.ws.broadcast import ConnectionManager

#: These tests drive the manager directly; any id will do as long as the
#: connect/broadcast pair agree on it.
SID = "test-session"


def make_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "gemini_api_key": None,
        "use_mock_agents": True,
        "rounds": 2,
        "turn_delay_seconds": 0.0,
        "pool_resource": "budget",
        "pool_total": 100.0,
        "_env_file": None,
    }
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def api() -> TestClient:
    return TestClient(create_app(make_settings()))


# --------------------------------------------------------------------------- #
# ConnectionManager in isolation
# --------------------------------------------------------------------------- #


class FakeSocket:
    def __init__(self, fail: bool = False) -> None:
        self.sent: list[str] = []
        self.fail = fail
        self.accepted = False

    async def accept(self) -> None:
        self.accepted = True

    async def send_text(self, payload: str) -> None:
        if self.fail:
            raise RuntimeError("socket closed")
        self.sent.append(payload)


async def test_broadcast_reaches_every_connected_client() -> None:
    manager = ConnectionManager()
    first, second = FakeSocket(), FakeSocket()
    await manager.connect(SID, first)  # type: ignore[arg-type]
    await manager.connect(SID, second)  # type: ignore[arg-type]

    await manager.broadcast(
        SID,
        RoundChangeMessage(payload=RoundChangePayload(round=2, total_rounds=6))
    )

    assert len(first.sent) == len(second.sent) == 1
    assert '"type":"round_change"' in first.sent[0].replace(" ", "")


async def test_a_dead_socket_is_dropped_and_does_not_block_the_others() -> None:
    manager = ConnectionManager()
    healthy, broken = FakeSocket(), FakeSocket(fail=True)
    await manager.connect(SID, healthy)  # type: ignore[arg-type]
    await manager.connect(SID, broken)  # type: ignore[arg-type]

    await manager.broadcast(
        SID,
        RoundChangeMessage(payload=RoundChangePayload(round=1, total_rounds=6))
    )

    assert len(healthy.sent) == 1
    assert manager.connection_count == 1


async def test_broadcasting_to_nobody_is_harmless() -> None:
    manager = ConnectionManager()

    await manager.broadcast(
        SID,
        RoundChangeMessage(payload=RoundChangePayload(round=1, total_rounds=6))
    )

    assert manager.connection_count == 0


async def test_disconnect_removes_the_socket() -> None:
    manager = ConnectionManager()
    socket = FakeSocket()
    await manager.connect(SID, socket)  # type: ignore[arg-type]

    await manager.disconnect(SID, socket)  # type: ignore[arg-type]

    assert manager.connection_count == 0


# --------------------------------------------------------------------------- #
# The endpoint
# --------------------------------------------------------------------------- #


def test_connecting_sends_a_full_state_frame_immediately(api: TestClient) -> None:
    sid = api.post("/api/session/start").json()["session_id"]

    with api.websocket_connect(f"/ws/negotiation/{sid}") as socket:
        frame = socket.receive_json()

    assert frame["type"] == "state"
    assert set(frame["payload"]) == {
        "round",
        "total_rounds",
        "pool",
        "agents",
        "trust_graph",
        "knowledge_graph",
        "offer_log",
        "agent_thoughts",
        "holdings",
        "closing_positions",
    }


def test_connecting_to_an_unknown_session_is_closed_not_left_hanging(
    api: TestClient,
) -> None:
    """Replaces the old "empty state" behaviour, deliberately.

    With one global session, connecting before it started meant "it may begin
    shortly", so an empty state was the useful answer. Session ids removed that
    reading: an id that does not resolve is expired or wrong and never will
    resolve, so the socket is closed and the client can say so.
    """
    with pytest.raises(WebSocketDisconnect) as caught:
        with api.websocket_connect("/ws/negotiation/nope") as socket:
            socket.receive_json()

    assert caught.value.code == WS_UNKNOWN_SESSION


def test_connecting_after_a_session_replays_the_current_state(api: TestClient) -> None:
    """A late client gets the full picture without a REST round-trip.

    The game is allowed to finish before connecting, so no broadcast can be in
    flight while the socket lives on a different portal loop than the request
    that started the session.
    """
    sid = api.post("/api/session/start").json()["session_id"]
    for _ in range(200):
        state = api.get(f"/api/session/{sid}/state").json()
        if state["closing_positions"] is not None:
            break
    else:  # pragma: no cover
        pytest.fail("the mock session never finished")

    with api.websocket_connect(f"/ws/negotiation/{sid}") as socket:
        payload = socket.receive_json()["payload"]

    assert len(payload["agents"]) == 4
    assert payload["round"] == 2
    assert payload["closing_positions"] is not None


# --------------------------------------------------------------------------- #
# The live stream: engine -> ConnectionManager -> socket
#
# Deliberately NOT driven through TestClient. Starlette's WebSocketTestSession
# runs the app in its own blocking portal, so an HTTP call made inside a
# `websocket_connect(...)` block lands on a different event loop from the one
# the socket belongs to, and broadcasting to it deadlocks. Driving the engine
# directly exercises the same wiring — the engine's emitter really is
# `manager.broadcast`, and the frames really are serialized and sent — while
# staying deterministic.
# --------------------------------------------------------------------------- #


async def run_game_into_sockets(count: int = 1, **settings_overrides: Any) -> list[FakeSocket]:
    from app.agents.mock_agent import RandomAgent
    from app.agents.personas import PERSONAS
    from app.engine.negotiation import NegotiationEngine

    manager = ConnectionManager()
    sockets = [FakeSocket() for _ in range(count)]
    for socket in sockets:
        await manager.connect(SID, socket)  # type: ignore[arg-type]

    engine = NegotiationEngine(
        session_id="ws-test",
        agents=[RandomAgent(p.id, seed=i) for i, p in enumerate(PERSONAS)],
        settings=make_settings(**settings_overrides),
        emit=manager.emitter_for(SID),
    )
    await engine.run()
    return sockets


def frames(socket: FakeSocket) -> list[dict[str, Any]]:
    import json

    return [json.loads(payload) for payload in socket.sent]


async def test_the_live_stream_carries_the_whole_game_through_to_reveal() -> None:
    (socket,) = await run_game_into_sockets(rounds=2)
    seen = [frame["type"] for frame in frames(socket)]

    assert seen[0] == "round_change"
    assert "thought" in seen
    assert seen[-1] == "closing"
    assert set(frames(socket)[-1]["payload"]) == {
        "positions",
        "final_state",
    }


async def test_offer_frames_use_the_from_alias_on_the_wire() -> None:
    (socket,) = await run_game_into_sockets(rounds=4)

    offers = [frame["payload"] for frame in frames(socket) if frame["type"] == "offer"]

    assert offers, "the random agents made no offers in four rounds"
    for payload in offers:
        assert "from" in payload
        assert "from_" not in payload


async def test_graph_updates_are_broadcast_alongside_offers() -> None:
    (socket,) = await run_game_into_sockets(rounds=4)
    seen = [frame["type"] for frame in frames(socket)]

    assert seen.count("graph_update") >= seen.count("offer")


async def test_every_client_receives_the_identical_stream() -> None:
    first, second = await run_game_into_sockets(count=2, rounds=2)

    assert first.sent == second.sent


async def test_an_injected_offer_is_broadcast_to_clients() -> None:
    from app.agents.mock_agent import ScriptedAgent
    from app.agents.personas import PERSONAS
    from app.engine.negotiation import NegotiationEngine
    from app.models.schemas import OfferSchema

    manager = ConnectionManager()
    socket = FakeSocket()
    await manager.connect(SID, socket)  # type: ignore[arg-type]

    engine = NegotiationEngine(
        session_id="inject",
        agents=[ScriptedAgent(p.id) for p in PERSONAS],
        settings=make_settings(rounds=1),
        emit=manager.emitter_for(SID),
    )

    await engine.inject_offer(
        OfferSchema(from_="human", to="maximizer", resource="budget", amount=7.0)
    )

    pushed = [frame for frame in frames(socket) if frame["type"] == "offer"]
    assert len(pushed) == 1
    assert pushed[0]["payload"]["from"] == "human"
    assert pushed[0]["payload"]["amount"] == 7.0
