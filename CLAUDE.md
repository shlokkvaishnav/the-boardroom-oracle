# Four Chairs — working notes

Three LLM agents argue a user-supplied topic, live, for six rounds. A human takes
the fourth seat. Two graphs record it: who trusts whom, and what was claimed.

Read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) before making a non-trivial
change — it explains the session lifecycle, both graphs, and the call budget.
This file is the short version plus the things that will bite you.

## Commands

```bash
cd backend && .venv/Scripts/python -m pytest -q    # 380+ tests, ~5s, no key needed
cd frontend && bun run typecheck                   # tsc --noEmit
cd frontend && bun run lint                        # eslint + prettier
```

On macOS/Linux the interpreter is `backend/.venv/bin/python`.

**Do not start a live session unprompted.** The user runs the backend themselves;
an unrequested session spends their API quota and collides with theirs.

## What this app is

It began as a scored bargaining game — hidden objectives, utility scores, a
winner. That was the less interesting half. The objectives and the scoring are
gone; the argument is the product.

**The resource pool still exists and is deliberately demoted.** It is a stake
anyone can put behind a position, never the subject. If you find code, prose, or
a prompt that still frames this as a game to win, it's a leftover — not a design
you should preserve.

## Architecture in six lines

- `engine/negotiation.py` is a plain state machine with an **injected `emit`
  callback**. It knows nothing about WebSockets or models. Keep it that way — it
  is why the whole suite runs with no key and no network.
- `main.py:create_app()` is the only composition root. Everything hangs off
  `app.state`.
- **Every optional component degrades to `None`** and every call site checks. No
  Gemini key ⇒ mock agents, no scribe, no rapporteur — and the session still
  completes.
- The engine's lock is **never held across a model call**. That is what lets a
  human speak or inject an offer mid-session.
- `models/schemas.py` is the wire contract; `models/agent_io.py` is the
  agent↔engine contract and is handed to Gemini as a response schema. Don't
  confuse them — anything added to `agent_io` becomes a field the model invents.
- The frontend touches backend field names in exactly one file:
  `lib/negotiation/adapter.ts`.

## Gotchas

**Line endings.** Several backend files are CRLF; the frontend is LF and Prettier
enforces it. Editing a file by reading and rewriting it in Python will silently
convert the whole file — read/write **bytes**, or use the editing tools.

**Prompt changes are behavioural.** Unit tests pin structure (the ledger is
omitted when empty; the turn prompt leads with what was said) but cannot tell you
the arguing got worse. Say so explicitly when you change one, and ask the user to
run a live session.

**"Dead code" needs more than a name grep.** `KnowledgeGraph.__contains__` looked
dead by name and was in use via the `in` operator. Run the tests before believing
a symbol is unreferenced.

**Adding an agent capability:** prefer a field on `AgentDecision` over a new
action, and a new action over a new provider call. Claims, stance and whispers
are all fields precisely because they ride a response already being sent — they
cost nothing. Whispering is deliberately *not* an action: if it consumed a turn,
nobody would ever do it.

**Concurrency doesn't buy throughput.** Provider quota is per API key, so every
call from every session queues behind one global slot. More sessions = slower
rounds.

## Conventions

- Test names are sentences: `test_the_pool_total_is_conserved_across_a_long_random_game`.
- Comments say **why**, not what. Non-obvious constants carry the judgement
  behind them. Match this density — it's the house style, not decoration.
- Prose in prompts and UI is written to be read aloud. Short sentences, plain
  words, no throat-clearing.
- Tests must never require a key, a network, or model weights.

## Known gap

The trust graph reacts to **offers and nothing else**, so in a discussion where
nobody trades it never moves off its uniform seed. The UI says "no trust signals
yet" rather than implying everyone assessed each other and landed on neutral.

Making trust react to argument is the next real design decision — the scribe
already emits `supports`/`contradicts` as data. The open questions and candidate
signals are written up in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#known-gap-trust-reacts-to-offers-and-nothing-else).
