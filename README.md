# Boardroom Oracle

A live multi-agent AI negotiation demo. Three LLM agents with distinct personas and hidden
objectives negotiate over a shared resource pool across a fixed number of rounds. A human can
join the table at any point by injecting a typed offer or speaking one aloud.

## Monorepo layout

| Path        | What it is                                                                 |
| ----------- | -------------------------------------------------------------------------- |
| `frontend/` | Built separately in Lovable. Not tracked in this repo (see `.gitignore`).   |
| `backend/`  | FastAPI service: negotiation engine, LLM agents, trust graph, WebSocket.    |

## Getting started

Everything backend-side runs in Docker — no local Python setup required.

```bash
docker compose -f backend/docker-compose.yml up --build
```

The API is then at <http://localhost:8000>, with interactive docs at
<http://localhost:8000/docs>.

See [`backend/README.md`](backend/README.md) for environment variables, the full API and
WebSocket contract, and how to run the test suite.
