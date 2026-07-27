# Development

Setup, tests, configuration. For how the thing actually works, read
[ARCHITECTURE.md](ARCHITECTURE.md).

---

## Fastest path

```bash
cp backend/.env.example backend/.env
docker compose up --build
```

Frontend <http://localhost:3000>, API <http://localhost:8000>, OpenAPI browser at
`/docs`. Works with no keys at all — you get scripted agents instead of real
ones, and the whole UI still demos.

> `VITE_BACKEND_URL` is compiled into the frontend bundle at **build** time, so
> `docker-compose.yml` passes it as a build arg, not an environment variable. It
> must be the URL the *browser* uses (`http://localhost:8000`), not the `api`
> service name — the REST and WebSocket calls are client-side.

---

## Native setup

Docker is not required. `ctranslate2` ships wheels for Python 3.13+, so the whole
stack including the Whisper voice pipeline runs natively on Python 3.12–3.14.

> **Windows PowerShell** has no `&&`. These are one command per block on purpose;
> chain with `;` if you want them on one line.

### Backend

```powershell
python -m venv backend\.venv
```

Note this is deliberately **not** `pip install -e .` — the backend is an
application, not a distributable package (`[tool.uv] package = false`, no
`[build-system]`), so dependencies install directly:

```powershell
backend\.venv\Scripts\python -m pip install fastapi "uvicorn[standard]" google-genai faster-whisper networkx pydantic pydantic-settings python-multipart tavily-python pytest pytest-asyncio httpx
```

```powershell
backend\.venv\Scripts\python -m uvicorn app.main:app --reload --port 8000 --app-dir backend
```

On macOS and Linux the interpreter is `backend/.venv/bin/python`.

### Frontend

Uses **Bun** — the lockfile is `bun.lock`, and `bunfig.toml` sets a 24-hour
supply-chain guard on newly published package versions.

```bash
cd frontend && bun install && bun run dev
```

Dev server on <http://localhost:8080>. **The port matters** — it's in the
backend's default `ALLOWED_ORIGINS`.

| Command | Does |
| --- | --- |
| `bun run dev` | Vite dev server, SSR + HMR |
| `bun run build` | Production build → `dist/client` + `dist/server` |
| `bun run typecheck` | `tsc --noEmit` |
| `bun run lint` | ESLint + Prettier |
| `bun run format` | Prettier `--write` |

---

## Tests

380+ tests, ~5 seconds, **no API key, no network, no model weights**. Run from
`backend/` so pytest finds `app`:

```bash
cd backend && .venv/Scripts/python -m pytest -q
```

If a change makes the suite need a key or a network, the change is wrong — mock
the seam instead.

| File | Covers |
| --- | --- |
| `test_engine.py` | The round loop, offers, transfers, human injection |
| `test_e2e.py` | Whole sessions, and the invariants that must hold however one goes |
| `test_trust_graph.py` | Every update rule, and that weights stay in `[0,1]` |
| `test_knowledge_graph.py` | Claim recording, entity merging, malformed-observer handling |
| `test_chair.py` | Turn reordering, and that nobody is ever starved of a turn |
| `test_whispers.py` | Private routing — who can and cannot see a whisper |
| `test_scribe.py` / `test_rapporteur.py` | Observers, and that both degrade safely |
| `test_schemas.py` | The exact serialized JSON, so a rename can't slip through |
| `test_ws.py` / `test_api.py` | The wire and the REST surface |
| `test_budget.py` | That the closing is always reserved before optional work |
| `test_llm_client.py` | Pacing, retries, backoff |

Frontend:

```bash
cd frontend && bun run typecheck
```

---

## Configuration

Every setting lives in `backend/app/config.py`, each with a comment explaining
the judgement behind its default. `backend/.env.example` mirrors it. The ones
that change behaviour most:

| Variable | Default | Why you'd touch it |
| --- | --- | --- |
| `GEMINI_API_KEY` | — | Absent ⇒ mock agents. Everything still runs. |
| `TAVILY_API_KEY` | — | Absent ⇒ agents can't search, but a topic session still works. |
| `GROQ_API_KEY` | — | Absent ⇒ local Whisper. Groq is ~200× realtime and more accurate. |
| `USE_MOCK_AGENTS` | `false` | Rehearse the full UI without spending quota. |
| `ROUNDS` | `6` | Session length. |
| `TURN_DELAY_SECONDS` | `2.5` | Pacing *and* rate-limit headroom in one dial. |
| `SESSION_CALL_BUDGET` | `60` | Hard ceiling on provider calls per session. `0` = unlimited. |
| `ENABLE_CHAIR` | `true` | `false` restores strict round-robin turn order. |
| `ENABLE_SCRIBE` | `true` | `false` drops `supports`/`contradicts` edges. |
| `ENABLE_SYNTHESIS` | `true` | `false` falls back to last-remark closing positions. |
| `MAX_CONCURRENT_SESSIONS` | `5` | See the warning below. |

> **Raising `MAX_CONCURRENT_SESSIONS` does not increase throughput.** Provider
> quota is per API *key*, so every agent call from every session queues behind
> the same global rate-limit slot. More sessions means slower rounds for all of
> them.

---

## Working on it

### Adding a WebSocket frame

1. Payload model in `models/schemas.py` (inherit `ContractModel`).
2. Frame class in `models/messages.py`, add it to the `WSMessage` union.
3. Emit it from `engine/negotiation.py` via `self._emit(...)` — never touch the
   socket directly.
4. Handle it in `lib/negotiation/live.ts:applyFrame`, with the snake→camel
   mapping in `adapter.ts`.
5. Pin the serialized shape in `test_schemas.py`.

### Adding something an agent can do

Prefer **a field on `AgentDecision`** over a new action, and a new action over a
new provider call. Claims, stance and whispers are all fields precisely because
they ride a response the agent was already sending — they cost nothing. A new
call costs a turn of latency against a shared rate limit.

If it must be an action, `Action` is read in `negotiation.py:_apply`,
`llm_agent.py:_sanitize`, the `agent_io.py` validators, `mock_agent.py`, and a
good number of tests. Check all of them.

### Changing a prompt

Prompts live in `agents/llm_agent.py` (the agents), `agents/scribe.py`,
`agents/rapporteur.py`, and `speech/parse_offer.py`.

These are **behavioural changes that unit tests cannot catch.** A few assertions
pin structural properties — that the ledger is omitted when empty, that the turn
prompt leads with what was said — but nothing can tell you the arguing got worse.
Run a live session on a topic with real disagreement in it and read the
transcript. Specifically watch:

- Did the arguing get **more concrete or more abstract**? Agents citing real
  numbers at each other is the bar.
- Do they still **trade at all**? Offers dropping to zero freezes the trust graph.
- Does **stance still move**? The drift chart depends on it.
- Are the personas still **distinct**? Rex should make the room uncomfortable;
  Mira's reciprocity should be visible and stated.

### House style

- Test names are sentences, not labels.
- Comments explain **why**, not what. Non-obvious constants carry the judgement
  behind them.
- Every optional dependency degrades to `None` and the session still finishes.
- The engine never learns about transport, and transport never learns about
  rules.
