# Documentation

| Doc | What's in it |
| --- | --- |
| **[ARCHITECTURE.md](ARCHITECTURE.md)** | How a session runs end to end — the engine, the agents, both graphs, the wire, the call budget. **Start here.** |
| **[DEVELOPMENT.md](DEVELOPMENT.md)** | Setup (Docker and native), tests, every environment variable, and how to add a frame or an agent capability. |
| **[BACKEND.md](BACKEND.md)** | The HTTP and WebSocket contract in detail, plus rate limiting, session capacity, and web search. |

Also at the repo root:

- [../README.md](../README.md) — what the project is, for someone who has never seen it
- [../CLAUDE.md](../CLAUDE.md) — conventions and gotchas for AI coding agents
- [../AGENTS.md](../AGENTS.md) — a pointer to the same, for non-Claude tools

`CLAUDE.md` and `AGENTS.md` stay at the root deliberately — coding agents
discover them there by convention, and moving them here would hide them.

## Images

Screenshots referenced by the root README belong in `docs/images/`. The
placeholders are already in the README as HTML comments — uncomment them once the
files exist:

| File | Shows |
| --- | --- |
| `images/hero.png` | The boardroom mid-session — graph, transcript, header |
| `images/graphs.png` | Trust and argument views side by side |
| `images/voice.png` | Speaking into the discussion |
