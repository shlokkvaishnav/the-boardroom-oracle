# Boardroom Oracle

A live multi-agent AI negotiation demo. Three LLM agents with distinct personas
and hidden objectives negotiate over a shared resource pool across a fixed
number of rounds. A human can join the table at any point by injecting a typed
offer or speaking one aloud. At the end, every hidden objective is revealed and
scored.

## Monorepo layout

| Path        | What it is                                                                  |
| ----------- | --------------------------------------------------------------------------- |
| `backend/`  | FastAPI service: negotiation engine, Claude agents, trust graph, WebSocket.  |
| `frontend/` | TanStack Start single-page arena: force-directed trust graph, thought feed, offer ledger, voice capture. |

## Running the backend

Everything runs in Docker — no host Python required (and in fact impossible
here: `faster-whisper`'s engine has no wheel for this machine's Python 3.14, so
the image pins 3.12).

```bash
cp backend/.env.example backend/.env   # add your ANTHROPIC_API_KEY
docker compose -f backend/docker-compose.yml up --build
```

API on <http://localhost:8000>, docs at `/docs`. Without a key it still runs,
falling back to mock agents.

Full setup, environment variables, the API and WebSocket contract, and the two
negotiation rules worth explaining live: [`backend/README.md`](backend/README.md).

## Running the frontend

```bash
cd frontend && npm install && npm run dev
```

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
