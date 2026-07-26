# Boardroom Oracle

A live multi-agent AI negotiation demo. Three LLM agents with distinct personas
and hidden objectives negotiate over a shared resource pool across a fixed
number of rounds. A human can join the table at any point by injecting a typed
offer or speaking one aloud. At the end, every hidden objective is revealed and
scored.

## Monorepo layout

| Path        | What it is                                                                  |
| ----------- | --------------------------------------------------------------------------- |
| `backend/`  | FastAPI service: negotiation engine, Gemini agents, trust graph, WebSocket.  |
| `frontend/` | TanStack Start single-page arena: force-directed trust graph, thought feed, offer ledger, voice capture. |

## Running the whole thing

Both halves are containerised, so this is the only command the demo needs:

```bash
cp backend/.env.example backend/.env   # add your GEMINI_API_KEY
docker compose up --build
```

Frontend on <http://localhost:3000>, API on <http://localhost:8000> with docs
at `/docs`. Without a key it still runs, falling back to mock agents. The web
container waits for the API's healthcheck before starting.

> `VITE_BACKEND_URL` is compiled into the frontend bundle at **build** time, so
> it is set as a build arg in `docker-compose.yml` rather than an environment
> variable. It must be the URL the *browser* uses (`http://localhost:8000`),
> not the `api` service name — the REST and WebSocket calls are client-side.

## Running without Docker

Docker is no longer required. `ctranslate2` now ships a wheel for Python 3.13+
(4.8.1 has `cp314`), so the whole stack — including the Whisper voice pipeline —
installs and runs natively on Python 3.12–3.14.

> **Windows PowerShell:** these are one command per block on purpose.
> PowerShell 5.1 has no `&&` — chain with `;` if you want them on one line.

Create the virtualenv:

```powershell
python -m venv backend\.venv
```

Install the dependencies. Note this is **not** `pip install -e .`: the backend is
an application, not a distributable package (`[tool.uv] package = false`, no
`[build-system]`), so its dependencies are installed directly.

```powershell
backend\.venv\Scripts\python -m pip install fastapi "uvicorn[standard]" google-genai faster-whisper networkx pydantic pydantic-settings python-multipart tavily-python pytest pytest-asyncio httpx
```

Run the API:

```powershell
backend\.venv\Scripts\python -m uvicorn app.main:app --reload --port 8000 --app-dir backend
```

Run the tests (from the `backend` directory, so pytest finds `app`):

```powershell
cd backend; .venv\Scripts\python -m pytest -q
```

On macOS or Linux the interpreter is at `backend/.venv/bin/python` and `&&`
works as usual.

## Running the backend on its own

No host Python required (and in fact impossible here: `faster-whisper`'s engine
has no wheel for this machine's Python 3.14, so the image pins 3.12). This
compose file mounts the source and runs uvicorn with `--reload`:

```bash
docker compose -f backend/docker-compose.yml up --build
```

Full setup, environment variables, the API and WebSocket contract, and the two
negotiation rules worth explaining live: [`backend/README.md`](backend/README.md).

## Running the frontend on its own

The frontend uses **Bun** — the lockfile is `bun.lock` and `bunfig.toml` sets a
24-hour supply-chain guard on new package versions.

```bash
cd frontend && bun install && bun run dev
```

Dev server on <http://localhost:8080> (the port matters — it is in the
backend's default `ALLOWED_ORIGINS`). Other scripts:

| Command | What it does |
| --- | --- |
| `bun run dev` | Vite dev server with SSR and HMR |
| `bun run build` | Production build → `dist/client` + `dist/server` |
| `bun run serve.ts` | Serve the build (static assets + SSR) on `:3000` |
| `bun run typecheck` | `tsc --noEmit` |
| `bun run lint` | ESLint + Prettier |
| `bun run format` | Prettier `--write` |

The app selects its data source automatically:

- **No `VITE_BACKEND_URL`** → a scripted six-round mock, so the UI demos offline.
- **`VITE_BACKEND_URL=http://localhost:8000`** → the real backend over REST +
  WebSocket. No component changes; only the client swaps.

## How the two halves talk

Each side keeps its own language's convention — `snake_case` on the wire and in
Python, `camelCase` in TypeScript — and a single adapter
(`frontend/src/lib/negotiation/adapter.ts`) translates between them, including
the trust-weight rescale (backend `0..1` with `0.5` neutral → renderer `-1..1`
with `0` neutral). Nothing else in the frontend touches a backend field name.
