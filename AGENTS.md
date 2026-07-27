# Agent instructions

This project's working notes for AI coding agents live in
**[CLAUDE.md](CLAUDE.md)**. Read that first — it is the same content regardless
of which tool you are.

Deeper context:

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — how a session runs end to end
- [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) — setup, tests, configuration
- [docs/BACKEND.md](docs/BACKEND.md) — the HTTP and WebSocket contract

The three things most likely to trip you up, repeated here so they're hard to
miss:

1. **Tests must run with no API key, no network, and no model weights.** If a
   change breaks that, the change is wrong — mock the seam instead.
2. **Don't start a live session unless asked.** The user runs the backend
   themselves; an unrequested run spends their quota.
3. **Some backend files are CRLF, the frontend is LF.** Rewriting a file through
   a text-mode script converts the whole thing silently. Use the editing tools,
   or read and write bytes.
