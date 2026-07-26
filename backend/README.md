# Boardroom Oracle — Backend

FastAPI service running a live multi-agent negotiation: three Gemini-backed
agents with distinct personas and hidden objectives trade a shared resource
pool over a fixed number of rounds, while a human can inject offers by typing
or by voice. State streams to the frontend over a WebSocket.

---

## Quick start

Everything runs in Docker. **No local Python setup is required or supported** —
see [Why Docker](#why-docker) below.

```bash
cp backend/.env.example backend/.env   # then paste your GEMINI_API_KEY
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

## Free-tier rate limits

The Gemini free tier is limited **per minute** (historically ~15 RPM on flash
models; verify the current numbers in AI Studio). A negotiation round fires one
call per persona, so the naive implementation — three concurrent calls — is the
fastest possible route to a 429.

All of this lives in **one file**, `app/llm_client.py`, which is the only module
in the codebase that touches a provider. Swapping provider or adding a paid key
is a change to that file alone; everything else calls
`generate_structured(prompt, schema) -> dict`.

Three defences, in order:

1. **Serialization.** A semaphore of one, plus a wait until
   `LLM_MIN_INTERVAL_SECONDS` has elapsed since the previous call started. A
   burst becomes a queue. At the 4s default, three agents take ~12s of calls —
   comfortably inside a 15 RPM budget.
2. **Backoff.** 429 and 5xx are retried with exponential backoff (2s, 4s, …)
   up to `LLM_MAX_ATTEMPTS`. 4xx that won't fix itself (400/403/404) is *not*
   retried — that would just burn quota.
3. **Pacing.** `TURN_DELAY_SECONDS` adds a deliberate gap between turns. It
   keeps the round inside the per-minute budget and happens to read as agents
   thinking, which is better for the demo than instant resolution.

These are all transport-level. A response that arrives fine but fails schema
validation is a *different* problem, handled one layer up by the agent, which
retries once with the validation error fed back into the prompt.

Structured output uses Gemini's native JSON mode (`response_mime_type` +
`response_schema`) rather than asking for JSON in the prompt — the model is
constrained rather than trusted. Pydantic validation stays as the safety net.

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
| `GEMINI_API_KEY` | *(none)* | Required for real agents. Without it, sessions run on mock agents. |
| `GEMINI_MODEL` | `gemini-3.6-flash` | Model for agent turns and voice parsing. **Free-tier eligibility changes** — check <https://aistudio.google.com/rate-limit> before a demo. |
| `GEMINI_MAX_OUTPUT_TOKENS` | `2048` | Per-turn output cap. |
| `LLM_MIN_INTERVAL_SECONDS` | `4.0` | Minimum gap between calls. See [Free-tier rate limits](#free-tier-rate-limits). |
| `LLM_MAX_ATTEMPTS` | `3` | Attempts per call on 429/5xx, including the first. |
| `LLM_BACKOFF_BASE_SECONDS` | `2.0` | First backoff wait; doubles each attempt. |
| `LLM_TIMEOUT_SECONDS` | `60.0` | Per-call timeout. |
| `USE_MOCK_AGENTS` | `false` | Force mock agents even with a key — rehearse without spending quota. |
| `ALLOWED_ORIGINS` | `localhost:3000,5173,8080` | Comma-separated CORS allowlist. **Add the frontend's deployed URL here.** |
| `CORS_ALLOW_ALL` | `false` | Dev escape hatch. Also disables credentialed CORS, which the spec forbids alongside a wildcard. |
| `ROUNDS` | `6` | Rounds per game. |
| `TURN_DELAY_SECONDS` | `2.5` | Pause between turns. Doubles as the demo's pacing dial and as rate-limit headroom. |
| `POOL_RESOURCE` | `budget` | Name of the contested resource. |
| `POOL_TOTAL` | `100.0` | Size of the pool, split evenly at the start. |
| `WHISPER_MODEL` | `base` | faster-whisper size (`tiny`…`large-v3`). |
| `WHISPER_COMPUTE_TYPE` | `int8` | `int8` is the right choice on CPU. |
| `WHISPER_PRELOAD` | `false` | Load the model at boot instead of first use. |
| `MAX_CONCURRENT_SESSIONS` | `5` | Live negotiations allowed at once. Raising it does **not** speed anything up — see [Concurrent sessions](#concurrent-sessions). |
| `SESSION_TTL_SECONDS` | `600` | Idle time before a session is swept. |
| `SESSION_SWEEP_INTERVAL_SECONDS` | `60` | How often the background sweeper runs. |
| `TAVILY_API_KEY` | *(none)* | Enables the agents' `web_search` tool. Without it a `context_topic` session still runs, just without lookups. See [Live web search](#live-web-search). |
| `TAVILY_MAX_RESULTS` | `3` | Results per search. Every hit is pasted into the follow-up prompt, so this is a prompt-size dial. |
| `TAVILY_TIMEOUT_SECONDS` | `10.0` | Per-search timeout. A timeout costs the agent a fact, not its turn. |

---

## API

### `POST /api/session/start`
Initialises a session and starts the round loop in the background. Any previous
session is stopped first.

The body is **optional**. A bare POST starts a plain negotiation:

```json
{ "session_id": "a1b2c3d4e5f6" }
```

Supplying a `context_topic` gives every party the same real-world premise and
switches the agents' `web_search` tool on — see
[Live web search](#live-web-search).

```bash
curl -X POST http://localhost:8000/api/session/start \
  -H 'content-type: application/json' \
  -d '{"context_topic": "the 2026 copper supply squeeze"}'
```

`context_topic` is capped at 500 characters, since it is prepended to every
agent's system prompt. Unknown fields are a `422`, not a silent no-op, so a
`contextTopic` typo fails loudly.

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
| `thought` | `{agent_id, text, timestamp, searched}` | Every turn, before the action. |
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

## Concurrent sessions

Several negotiations can run at once. Each has its own engine, trust graph,
offer log and background round loop, keyed by a `session_id` minted by
`POST /api/session/start`. Every other endpoint is scoped to that id — in the
**path**, consistently, including the WebSocket.

```
POST /api/session/start                     -> { "session_id": "a1b2c3d4e5f6" }
GET  /api/session/{id}/state
POST /api/session/{id}/inject-offer
POST /api/session/{id}/voice-offer
POST /api/session/{id}/reset
WS   /ws/negotiation/{id}
```

A frame from one negotiation never reaches a viewer of another, and resetting
your own session cannot stop anyone else's.

### More users means slower rounds, not more throughput

This is the part worth understanding before wondering why it feels sluggish.

`llm_client.py` holds a **semaphore of one and a 4s start-to-start throttle,
shared globally across every session**. There is one `LLMClient` for the whole
process, so an agent turn in session A queues behind an agent turn in session B.

That is deliberate, and it is not a bottleneck to optimise away. The limit being
protected is a **per-API-key quota**, not a per-session one. Giving each session
its own client or its own semaphore would fire N concurrent calls at the same
key and multiply the 429 rate by N. The code says so at the point where someone
would be tempted to change it, and `tests/test_sessions.py` pins the behaviour:
six calls fired as if from two sessions still show `max_concurrent == 1`.

The visible consequence, for a 6-round game:

| Live sessions | Plain | With `context_topic` |
| --- | --- | --- |
| 1 | ~2 min | ~3–4 min |
| 3 | ~6 min | ~10 min |
| 5 (default cap) | ~10 min | ~20 min |

**Concurrency here buys isolation, not speed.** Five people can each watch their
own negotiation without corrupting each other's; they cannot each watch a fast
one.

### Capacity

`MAX_CONCURRENT_SESSIONS` (default 5) bounds live games. Beyond it,
`/api/session/start` returns **503** with a `Retry-After` header and a readable
message; the frontend shows a "table full" banner rather than an error.

503 rather than 429 on purpose: nothing about the caller is being rate-limited,
the box is simply full. Only *unfinished* sessions count — a finished game makes
no provider calls, so holding the next player out on its account would achieve
nothing.

### Expiry

A session untouched for `SESSION_TTL_SECONDS` (default 600) is swept: its loop
is stopped, its engine dropped, and the eviction logged. A background sweeper
runs every `SESSION_SWEEP_INTERVAL_SECONDS`, and `start` also sweeps before
checking capacity, so nobody is refused on account of sessions that already aged
out. Any REST read counts as activity, so a session being watched never expires
under its viewer.

The frontend keeps its id in `sessionStorage`, so a refresh rejoins the game in
progress; if the id no longer resolves it shows a "session ended" state instead
of a dead board.

---

## Live web search

Agents can look things up mid-negotiation. It is off unless you ask for it: the
tool is offered only when a session is started with a `context_topic`, so an
ordinary game is byte-for-byte what it was before the feature existed.

The two halves work together. `context_topic` sets the premise — "you are
negotiating resource allocation in the context of: the 2026 copper supply
squeeze" — and `web_search` is how an agent gets a current fact to argue with.
The premise on its own still works without a Tavily key; agents simply reason
from it without being able to check anything.

### The turn, in two phases

```
no context_topic  ->  one structured call. No tools sent. Unchanged.
context_topic set ->  call 1: tools offered, no schema. "Do you need a fact?"
                      |- yes -> Tavily runs -> call 2: results in the prompt
                      `- no  -> call 2 anyway (the prose from call 1 is dropped)
```

**Why two calls rather than one.** Gemini rejected `response_schema` alongside
function declarations outright until the Gemini 3 series, where the combination
is still preview and documented only against the newer Interactions API rather
than `generate_content`, which is what this service uses. Splitting the turn
works on any version and leaves the structured call identical to a no-tools
turn. The cost is that a search-enabled turn is always two calls even when the
model declines to search — which is exactly why the whole thing is gated.

**One search per turn, enforced in code.** `_maybe_search` runs once and honours
a single tool call, so a model that asks three times gets one. The cap is not a
request in the prompt, because worst-case round latency shouldn't depend on the
model choosing to behave.

**Every failure degrades to an ordinary turn.** Probe fails, search fails, empty
query, unknown tool name, no key — the agent loses a fact, never its move. Same
principle as the safe-default decision in `agents/llm_agent.py`.

### `searched`

`AgentThought` carries a `searched` list, non-empty only on turns where a search
actually ran:

```json
{ "query": "copper price 2026",
  "result_snippet": "Copper hit $4.20/lb.",
  "source_url": "https://a.example" }
```

It is **stamped server-side from the call that really happened**, and is
deliberately *not* a field on `AgentDecision`. That model is handed to Gemini as
`response_schema`, so anything declared on it becomes something the model is
asked to invent — `searched` would have been hallucinated provenance. It lives
on the internal `TurnDecision` subclass instead.

### What it costs

Provider calls, for the default three agents over six rounds:

| | Gemini calls | Pacing floor at 4s | Effective RPM |
| --- | --- | --- | --- |
| No `context_topic` | 18 | 72s | ~9 |
| `context_topic` set | 36 | 144s | 15 |

**A search-enabled game sits on the free tier's ~15 RPM ceiling with no
headroom.** Raise `LLM_MIN_INTERVAL_SECONDS` to 5.0 for these sessions — it
costs ~30s of runtime and buys margin. Expect a search-enabled six-round game to
take 3–4 minutes rather than ~2.

Each round logs what it actually spent, which is the number to watch in
rehearsal:

```
round 3 of session a1b2c3d4e5f6 used 6 provider call(s)
```

`boardroom.search` also logs every query and its hit count, so you can see
exactly what an agent looked up and when.

---

## Frontend integration

Each side uses its own language's convention — `snake_case` over the wire and in
Python, `camelCase` in TypeScript — and **one adapter bridges them**:
`frontend/src/lib/negotiation/adapter.ts`. Components never see a backend key.

To run the pair locally, `docker compose up --build` from the repo root brings
up both. To point a **Bun** dev server at a containerised backend instead:

```bash
docker compose -f backend/docker-compose.yml up --build     # API on :8000
cd frontend && bun install && VITE_BACKEND_URL=http://localhost:8000 bun run dev
```

The dev server binds `:8080`, which is already in the `ALLOWED_ORIGINS` default
— serving it from another port means adding that origin, or the REST calls fail
CORS while the WebSocket (exempt from CORS) still connects, which is a
confusing half-working state.

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

**214 tests. No API key, no network, and no model weights required** — every
Gemini call and every Whisper call is mocked at the seam. That's the point of
`agents/base.py:Agent` and `speech/transcribe.py:Transcriber`: the engine can't
tell a scripted agent from a real one. `test_llm_client.py` covers the pacing
and backoff behaviour directly, including that calls never run concurrently.

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
  llm_client.py       THE ONLY MODULE THAT TALKS TO A PROVIDER (pacing, backoff)
  api/routes.py       REST endpoints
  api/ws.py           /ws/negotiation
  models/schemas.py   the frontend wire contract
  models/messages.py  WS discriminated union
  models/agent_io.py  AgentDecision — what an agent turn must return
  agents/personas.py  the cast, styles, and hidden objectives
  agents/base.py      Agent protocol + TurnContext — the swap point
  agents/llm_agent.py Gemini-backed agent, retry then safe default
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
