# Boardroom Oracle — Backend

FastAPI service running a live multi-agent negotiation: three Claude-backed
agents with distinct personas and hidden objectives trade a shared resource
pool over a fixed number of rounds, while a human can inject offers by typing
or by voice. State streams to the frontend over a WebSocket.

---

## Quick start

Everything runs in Docker. **No local Python setup is required or supported** —
see [Why Docker](#why-docker) below.

```bash
cp backend/.env.example backend/.env   # then paste your ANTHROPIC_API_KEY
docker compose -f backend/docker-compose.yml up --build
```

- API: <http://localhost:8000>
- Interactive docs: <http://localhost:8000/docs>
- Health: <http://localhost:8000/health>

Without an API key the service still runs — it falls back to mock agents and
logs a warning — so the demo is exercisable end to end before any credentials
exist.

Drive a session:

```bash
curl -X POST http://localhost:8000/api/session/start
```

Then watch the frames arrive on `ws://localhost:8000/ws/negotiation`.

---

## Why Docker

Not a preference — a hard requirement of the dependency tree.

`faster-whisper` runs on `ctranslate2`, which publishes **no wheel for Python
3.13+**. This machine has only Python 3.14, so the stack physically cannot be
installed on the host. The image pins **Python 3.12**, where every dependency
has a prebuilt wheel.

Two other things the Dockerfile does deliberately:

- **`libgomp1` is installed explicitly.** `ctranslate2` links against it and
  `import faster_whisper` fails outright on `python:*-slim` without it.
- **Whisper weights live in a named volume** (`whisper-cache`), so the model
  downloads once rather than on every container start. Set
  `WHISPER_PRELOAD=true` to load it at boot instead of during the first voice
  request — worth doing before a live demo.

### Why uv

`uv` handles dependency management, and lives **inside the image** — nothing to
install on the host. It was chosen over Poetry for materially faster image
builds, first-class `--frozen` lockfile installs, and an official image to copy
the binary from. `uv.lock` is committed; the build uses `uv sync --frozen`, so
builds are reproducible.

To change dependencies, edit `pyproject.toml` and regenerate the lock **in a
container** (the host has no `uv`):

```bash
docker run --rm -v "$PWD/backend:/srv" -w /srv \
  ghcr.io/astral-sh/uv:python3.12-bookworm-slim uv lock
```

---

## Environment variables

All optional except the API key. Copy `.env.example` to `.env`; it is gitignored.

| Variable | Default | Purpose |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | *(none)* | Required for real agents. Without it, sessions run on mock agents. |
| `ANTHROPIC_MODEL` | `claude-opus-5` | Model used for agent turns and voice parsing. |
| `ANTHROPIC_EFFORT` | `low` | `low`…`max`. Controls reasoning depth, and therefore **per-turn latency**. Raise it if agents feel shallow; keep it low to keep a live demo moving. |
| `ANTHROPIC_MAX_TOKENS` | `2048` | Per-turn output cap. |
| `USE_MOCK_AGENTS` | `false` | Force mock agents even with a key — rehearse without spending tokens. |
| `ALLOWED_ORIGINS` | `localhost:3000,5173,8080` | Comma-separated CORS allowlist. **Add the frontend's deployed URL here.** |
| `CORS_ALLOW_ALL` | `false` | Dev escape hatch. Also disables credentialed CORS, which the spec forbids alongside a wildcard. |
| `ROUNDS` | `6` | Rounds per game. |
| `TURN_DELAY_SECONDS` | `3.0` | Pause between turns. This is the demo's pacing dial — it's what gives the audience time to read each move. |
| `POOL_RESOURCE` | `budget` | Name of the contested resource. |
| `POOL_TOTAL` | `100.0` | Size of the pool, split evenly at the start. |
| `WHISPER_MODEL` | `base` | faster-whisper size (`tiny`…`large-v3`). |
| `WHISPER_COMPUTE_TYPE` | `int8` | `int8` is the right choice on CPU. |
| `WHISPER_PRELOAD` | `false` | Load the model at boot instead of first use. |

---

## API

### `POST /api/session/start`
Initialises a session and starts the round loop in the background. Any previous
session is stopped first.

```json
{ "session_id": "a1b2c3d4e5f6" }
```

### `GET /api/session/state`
The full `NegotiationState`. `404` if no session is running.

### `POST /api/session/inject-offer`
```json
{ "from": "human", "to": "maximizer", "resource": "budget", "amount": 12.0 }
```
Returns the updated `NegotiationState`. The offer becomes pending immediately,
so the next agent to act sees it and can accept, reject, or ignore it.

`400` with a readable reason for an invalid offer (unknown recipient, wrong
resource, non-positive amount, more than the sender holds). `422` for a
malformed body — including unknown fields, which are rejected rather than
silently dropped.

### `POST /api/session/voice-offer`
`multipart/form-data` with a `file` field (webm/ogg/wav/mp4, ≤ 25 MB).

```json
{
  "transcript": "give the maximizer twelve budget",
  "parsed_offer": { "from": "human", "to": "maximizer", "resource": "budget", "amount": 12.0 },
  "confidence": "high"
}
```

**This endpoint changes no game state.** It previews only; the frontend shows
the transcript and parsed offer for confirmation and then calls
`/inject-offer`. `parsed_offer` is `null` when the speech couldn't be resolved
into a valid offer. `confidence` is `high` only when the audio was clear **and**
the parse succeeded — either one failing yields `low`.

`503` if transcription is unavailable, `400` on an empty upload, `413` if oversized.

### `POST /api/session/reset`
Tears the session down and stops its background loop. Safe to call when nothing
is running (`{"status": "no-session"}`).

---

## WebSocket — `/ws/negotiation`

On connect the client receives **one `state` frame** with the full
`NegotiationState`, then incremental frames as the game proceeds. If no session
is running, the `state` frame is a well-formed empty state rather than nothing.

Every frame is `{"type": ..., "payload": {...}}`:

| `type` | `payload` | Emitted when |
| --- | --- | --- |
| `state` | `NegotiationState` | On connect, and after a reset. |
| `round_change` | `{round, total_rounds}` | At the start of each round. |
| `thought` | `{agent_id, text, timestamp}` | Every turn, before the action. |
| `offer` | `OfferRecord` | An offer is made, and again when answered (with `accepted` stamped). |
| `graph_update` | `{edges: [...], reason}` | After every offer event. `reason` is `offer_made`, `offer_accepted` or `offer_rejected`. |
| `reveal` | `{revealed_objectives, scores, holdings, final_state}` | Once, at the end. |

> **`state` is an addition to the type list in the original spec.** The spec
> required sending a full `NegotiationState` on connect but listed no variant
> able to carry one (its list was prefixed "e.g."). Everything else matches the
> spec exactly.

The socket is push-only; the server ignores anything the client sends and uses
it solely to detect disconnects.

---

## Frontend integration

Each side uses its own language's convention — `snake_case` over the wire and in
Python, `camelCase` in TypeScript — and **one adapter bridges them**:
`frontend/src/lib/negotiation/adapter.ts`. Components never see a backend key.

| Backend sends | Frontend uses | Handled by |
| --- | --- | --- |
| `is_human`, `agent_id`, `last_offer_accepted` | `isHuman`, `agentId`, `lastOfferAccepted` | `toAgent` / `toThought` / `toEdge` |
| `trust_graph`, `offer_log`, `agent_thoughts`, `revealed_objectives` | `trustGraph`, `offerLog`, `agentThoughts`, `revealedObjectives` | `toState` |
| `weight` in `0..1`, **0.5 neutral** | `weight` in `-1..1`, **0 neutral** | `toSignedWeight` (`w * 2 - 1`) |
| `last_offer_accepted: bool \| null` | `lastOfferAccepted: boolean` | `=== true` |
| `{transcript, parsed_offer, confidence}` | `{transcript, offer, confidence}` | `toVoiceResult` |

`LiveNegotiationClient` also had to be corrected in five places to talk to this
API — worth knowing about, since each was a silent failure rather than an error:

1. **Frames are deltas, not snapshots.** It treated any payload with a `round`
   field as a whole `NegotiationState`. Both `offer` and `round_change` payloads
   carry `round`, so every offer wiped the board. It now switches on `type` and
   merges incrementally; only `state` and the reveal's `final_state` replace.
2. **`reset()` posted to `/api/session/start`** with `{reset: true}` — which
   starts a *new* negotiation. Now uses `/api/session/reset`.
3. **Voice upload used the field name `audio`**; FastAPI binds `file`, so every
   upload was a 422.
4. **It read `result.offer`**, but the endpoint returns `parsed_offer`, so the
   confirm button never appeared.
5. **Errors were swallowed** — a 400 from `inject-offer` (which carries a
   readable reason) looked like success.

**Not yet surfaced:** the `reveal` frame carries real `scores` — each agent's
measured objective achievement in `0..1`. `RevealOverlay` currently derives its
own percentage from net accepted credits instead. Wiring `scores` through would
make the endgame numbers real rather than a proxy.

---

## The two rules you'll explain live

Both are isolated, named, and free of magic numbers so they can be narrated
while the demo runs.

### Trust graph — `app/engine/trust_graph.py`

Edges point **from the truster to the trusted**: `weight(A → B)` is *how much A
trusts B*, in `[0, 1]`, starting at `0.5`.

```
favorability = amount / pool_total          # generosity, normalised to the pool

A offers to B        →  B trusts A more:   weight(B→A) += 0.15 × favorability
B accepts A's offer  →  A trusts B more:   weight(A→B) += 0.20 × (0.5 + favorability)
B rejects A's offer  →  A trusts B less:   weight(A→B) -= 0.15
```

Accept and reject move the **same** edge in opposite directions, so one answer
visibly swings the offerer's trust — that's the moment to point at. Making an
offer moves the *other* edge, so generosity is rewarded before anyone replies.
Normalising by the pool makes the rule pool-size independent. All four constants
live in `TrustTuning`.

### Opponent model — `app/agents/opponent_model.py`

Each agent keeps a private read on every other party, all exponential moving
averages (`updated = 0.6 × previous + 0.4 × observation`):

- `acceptance_rate` — how often they accept my offers
- `perceived_aggressiveness` — how stingy their offers to me are
- `trust_score` — the agent's **own** running score, moved only by deltas it
  reports itself

`ALPHA = 0.4` means a belief moves ~40% toward each new observation, so agents
adapt within two or three rounds — fast enough to see in six.

Note the split: the **graph** is the objective public record drawn in the UI;
`trust_score` is the agent's subjective read. The gap between them is usually
the most interesting thing on screen, and both are shown in the agent's prompt.

---

## Testing

```bash
docker compose -f backend/docker-compose.yml run --rm --no-deps api pytest -q
```

**201 tests. No API key, no network, and no model weights required** — every
Claude call and every Whisper call is mocked at the seam. That's the point of
`agents/base.py:Agent` and `speech/transcribe.py:Transcriber`: the engine can't
tell a scripted agent from a real one.

Two things worth knowing before editing tests:

- **WS live-stream tests drive `engine → ConnectionManager → socket`
  directly**, not through `TestClient`. Starlette's `WebSocketTestSession` runs
  the app in its own blocking portal, so an HTTP call made inside a
  `websocket_connect(...)` block lands on a *different event loop* than the
  socket, and broadcasting to it deadlocks. The direct path covers the same
  wiring deterministically.
- **API tests use a long `TURN_DELAY_SECONDS`.** At zero delay, mock agents
  finish an entire game between two HTTP calls, and the engine then correctly
  refuses to inject into a finished session.

---

## Layout

```
app/
  main.py             composition root — everything shared hangs on app.state
  config.py           pydantic-settings
  api/routes.py       REST endpoints
  api/ws.py           /ws/negotiation
  models/schemas.py   the frontend wire contract
  models/messages.py  WS discriminated union
  models/agent_io.py  AgentDecision — what an agent turn must return
  agents/personas.py  the cast, styles, and hidden objectives
  agents/base.py      Agent protocol + TurnContext — the swap point
  agents/llm_agent.py Claude-backed agent, retry then safe default
  agents/mock_agent.py scripted + seeded-random doubles
  agents/opponent_model.py belief state
  engine/negotiation.py the state machine
  engine/trust_graph.py networkx wrapper + update rule
  engine/scoring.py   endgame objective scoring
  session/store.py    SessionStore protocol + in-memory impl
  ws/broadcast.py     connection manager
  speech/             Whisper + transcript→offer parsing
```

`engine/` imports nothing from FastAPI. Session state is reached only through
`SessionStore`, so swapping in Redis or Postgres touches no game logic.

---

## Known limitations

- **One concurrent session**, as specified. The store is keyed for more, but
  `start` replaces rather than multiplexes.
- **State is in memory** — a restart loses the game.
- Offers left unanswered when the last round ends stay `accepted: null`.
- `starlette` warns that `TestClient` prefers `httpx2` over `httpx`. Harmless
  today; when it hard-breaks, swap the dev dependency.
