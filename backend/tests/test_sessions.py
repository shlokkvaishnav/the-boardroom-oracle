"""Concurrent sessions: isolation, capacity, expiry, and the shared queue.

The four properties that make multi-user safe. Isolation is the promise;
capacity and TTL are what keep a small box alive; the shared rate-limit queue
is the one thing sessions deliberately do *not* get their own copy of.

No network and no clock-watching: the store takes an injectable clock so TTL is
tested by fast-forwarding rather than by sleeping for ten minutes in CI.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.agents.mock_agent import ScriptedAgent
from app.config import Settings
from app.engine.negotiation import NegotiationEngine
from app.llm_client import LLMClient
from app.main import create_app
from app.models.agent_io import AgentDecision
from app.session.store import AtCapacity, InMemorySessionStore

from tests.test_llm_client import FakeSDK, Sample


def make_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "gemini_api_key": None,
        "use_mock_agents": True,
        "rounds": 2,
        "turn_delay_seconds": 30.0,  # keep games alive across HTTP calls
        "pool_resource": "budget",
        "pool_total": 100.0,
        "_env_file": None,
    }
    base.update(overrides)
    return Settings(**base)


class Clock:
    """A hand-wound monotonic clock."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def build_engine(session_id: str, settings: Settings | None = None) -> NegotiationEngine:
    settings = settings or make_settings()
    return NegotiationEngine(
        session_id=session_id,
        agents=[ScriptedAgent(persona, [AgentDecision(action="pass", thought="…")]) for persona in ("cooperator", "maximizer", "titfortat")],
        settings=settings,
    )


# --------------------------------------------------------------------------- #
# Isolation
# --------------------------------------------------------------------------- #


def test_two_sessions_have_independent_state() -> None:
    """The core promise: an offer in A must never surface in B."""
    client = TestClient(create_app(make_settings()))

    a = client.post("/api/session/start").json()["session_id"]
    b = client.post("/api/session/start").json()["session_id"]

    assert a != b

    injected = client.post(
        f"/api/session/{a}/inject-offer",
        json={"from": "human", "to": "maximizer", "resource": "budget", "amount": 7.0},
    )
    assert injected.status_code == 200

    state_a = client.get(f"/api/session/{a}/state").json()
    state_b = client.get(f"/api/session/{b}/state").json()

    assert any(offer["amount"] == 7.0 for offer in state_a["offer_log"])
    assert all(offer["amount"] != 7.0 for offer in state_b["offer_log"])


def test_resetting_one_session_leaves_the_other_running() -> None:
    client = TestClient(create_app(make_settings()))
    a = client.post("/api/session/start").json()["session_id"]
    b = client.post("/api/session/start").json()["session_id"]

    client.post(f"/api/session/{a}/reset")

    assert client.get(f"/api/session/{a}/state").status_code == 404
    assert client.get(f"/api/session/{b}/state").status_code == 200


async def test_frames_only_reach_sockets_watching_that_session() -> None:
    from app.models.messages import RoundChangeMessage, RoundChangePayload
    from app.ws.broadcast import ConnectionManager

    class FakeSocket:
        def __init__(self) -> None:
            self.sent: list[str] = []

        async def accept(self) -> None: ...

        async def send_text(self, payload: str) -> None:
            self.sent.append(payload)

    manager = ConnectionManager()
    watcher_a, watcher_b = FakeSocket(), FakeSocket()
    await manager.connect("a", watcher_a)  # type: ignore[arg-type]
    await manager.connect("b", watcher_b)  # type: ignore[arg-type]

    emit_a = manager.emitter_for("a")
    await emit_a(RoundChangeMessage(payload=RoundChangePayload(round=3, total_rounds=6)))

    assert len(watcher_a.sent) == 1
    assert watcher_b.sent == []


# --------------------------------------------------------------------------- #
# Capacity
# --------------------------------------------------------------------------- #


async def test_the_store_refuses_a_session_beyond_the_cap() -> None:
    store = InMemorySessionStore(max_sessions=2)

    await store.put(build_engine("one"))
    await store.put(build_engine("two"))

    with pytest.raises(AtCapacity):
        await store.put(build_engine("three"))


async def test_a_finished_session_does_not_count_against_the_cap() -> None:
    """It makes no provider calls, so holding the next player out is pointless."""
    store = InMemorySessionStore(max_sessions=1)
    done = build_engine("done")
    done.finished = True
    await store.put(done)

    await store.put(build_engine("fresh"))  # must not raise

    assert await store.active_count() == 1


async def test_re_putting_the_same_id_is_not_a_new_session() -> None:
    store = InMemorySessionStore(max_sessions=1)
    await store.put(build_engine("same"))

    await store.put(build_engine("same"))  # must not raise


def test_starting_at_capacity_is_a_503_with_a_readable_message() -> None:
    client = TestClient(create_app(make_settings(max_concurrent_sessions=2)))

    client.post("/api/session/start")
    client.post("/api/session/start")
    response = client.post("/api/session/start")

    assert response.status_code == 503
    assert "capacity" in response.json()["detail"].lower()
    assert response.headers["Retry-After"] == "120"


def test_capacity_frees_up_after_a_reset() -> None:
    client = TestClient(create_app(make_settings(max_concurrent_sessions=1)))
    first = client.post("/api/session/start").json()["session_id"]

    assert client.post("/api/session/start").status_code == 503

    client.post(f"/api/session/{first}/reset")

    assert client.post("/api/session/start").status_code == 200


# --------------------------------------------------------------------------- #
# Expiry
# --------------------------------------------------------------------------- #


async def test_an_idle_session_is_swept_after_the_ttl() -> None:
    clock = Clock()
    store = InMemorySessionStore(ttl_seconds=600.0, clock=clock)
    await store.put(build_engine("stale"))

    clock.advance(601)
    swept = await store.sweep()

    assert swept == ["stale"]
    assert await store.get("stale") is None


async def test_a_session_touched_within_the_window_survives() -> None:
    clock = Clock()
    store = InMemorySessionStore(ttl_seconds=600.0, clock=clock)
    await store.put(build_engine("busy"))

    clock.advance(500)
    await store.get("busy")  # a read counts as activity
    clock.advance(500)
    swept = await store.sweep()

    assert swept == []
    assert await store.get("busy") is not None


async def test_sweeping_frees_capacity() -> None:
    clock = Clock()
    store = InMemorySessionStore(max_sessions=1, ttl_seconds=600.0, clock=clock)
    await store.put(build_engine("old"))

    clock.advance(601)
    await store.put(build_engine("new"))  # put() sweeps first, so this fits

    assert await store.get("old") is None
    assert await store.get("new") is not None


# --------------------------------------------------------------------------- #
# The shared rate-limit queue
#
# The one thing sessions do NOT get their own copy of. If this ever fails,
# something has given each session its own client or semaphore, and the
# per-API-key quota is being hit N times harder than the pacing assumes.
# --------------------------------------------------------------------------- #


async def test_the_llm_queue_is_shared_across_concurrent_sessions() -> None:
    from tests.test_llm_client import make_settings as llm_settings

    sdk = FakeSDK(*['{"value": "ok"}'] * 6)
    shared = LLMClient(llm_settings(), client=sdk)

    # Six calls fired as if from two sessions of three agents each.
    await asyncio.gather(
        *(shared.generate_structured(f"session-a-agent-{i}", Sample) for i in range(3)),
        *(shared.generate_structured(f"session-b-agent-{i}", Sample) for i in range(3)),
    )

    assert len(sdk.models.calls) == 6
    assert sdk.models.max_concurrent == 1, (
        "two sessions ran provider calls concurrently — the rate-limit queue is "
        "no longer global"
    )


async def test_pacing_applies_across_sessions_not_just_within_one() -> None:
    from tests.test_llm_client import make_settings as llm_settings

    sdk = FakeSDK(*['{"value": "ok"}'] * 4)
    shared = LLMClient(llm_settings(llm_min_interval_seconds=0.08), client=sdk)

    await asyncio.gather(
        *(shared.generate_structured(f"a{i}", Sample) for i in range(2)),
        *(shared.generate_structured(f"b{i}", Sample) for i in range(2)),
    )

    gaps = [
        sdk.models.started_at[i + 1] - sdk.models.started_at[i]
        for i in range(len(sdk.models.started_at) - 1)
    ]
    assert all(gap >= 0.07 for gap in gaps), gaps
