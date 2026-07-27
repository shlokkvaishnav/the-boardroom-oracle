# Architecture

How a session actually runs, from `POST /start` to the closing frame. Written to
be read top to bottom by someone who has never seen this codebase.

If you only read one thing: **the engine is a plain state machine with an
injected emitter.** It has no idea a WebSocket exists, no idea a model exists,
and runs to completion in tests with neither. Everything else is arranged around
keeping that true.

---

## 1. The shape of it

```
browser ──REST──►  api/routes.py  ──►  engine/negotiation.py  ──► agents/*
   ▲                                          │
   └────────WS──── ws/broadcast.py ◄──────────┘  (emit callback)
```

Four seats: **Ada** (Cooperator), **Rex** (Maximizer), **Mira** (TitForTat), and
**you**. The three AI seats take turns; the human seat acts out of band, whenever
you press a button.

A session is six rounds. Each round, every agent acts exactly once.

### The one seam that matters

`NegotiationEngine` is constructed with an `emit` callback
(`api/routes.py` hands it `manager.emitter_for(session_id)`). It calls that
callback and never touches transport. That is why:

- the whole engine is testable with `emit = list.append`
- a session keeps running with zero browsers attached
- adding a new frame type is a one-line change in one place

Break this seam and most of the test suite becomes integration tests.

---

## 2. Composition root

Everything is wired once, in `main.py:create_app()`, onto `app.state`:

| `app.state.*` | Built when |
| --- | --- |
| `settings` | always |
| `store` | always — `InMemorySessionStore` |
| `manager` | always — `ConnectionManager`, owns the sockets |
| `transcriber` | Groq if `GROQ_API_KEY`, else local faster-whisper |
| `search_tool` | `TAVILY_API_KEY` set |
| `llm_client`, `offer_parser` | `GEMINI_API_KEY` set |
| `scribe` | Gemini key **and** `ENABLE_SCRIBE` |
| `rapporteur` | Gemini key **and** `ENABLE_SYNTHESIS` |

**Every optional component degrades to `None`, and every call site checks.** No
key means mock agents and no scribe — the app still runs a complete session.
This is load-bearing for the demo and for CI.

---

## 3. A session, step by step

### Start

`POST /api/session/start` → `routes.py:start_session`

1. Mint a 12-char session id.
2. Build the agent list — real `LLMAgent`s if a key is configured, seeded
   `RandomAgent`s otherwise.
3. Construct the engine with the emitter, the scribe, and the rapporteur.
4. `store.put(engine)` — raises `AtCapacity` → **503** if too many are live.
5. `engine.start()` — fires `asyncio.create_task(self.run())` and returns
   immediately. The HTTP response does not wait for the session.

The frontend then does `GET /{id}/state` to seed the roster, and opens the
socket. Both are needed: the socket sends one `state` frame on connect, but the
REST seed is what fills the UI before the first frame arrives.

### The round loop — `engine/negotiation.py:run()`

For each round:

1. **Can we afford it?** `budget.can_afford(len(agents))`. If not, mark
   `ended_early` and `break` — deliberately not `return`, so the closing still
   runs. **A session can end early; it always ends.**
2. Decide whether search is affordable this round (`can_afford_extra`).
3. Emit `round_change`.
4. For each agent, in the order the chair decides: `_take_turn(agent)`, then
   sleep `TURN_DELAY_SECONDS`.
5. Fire the scribe as a **background task**, never awaited inside a turn.

Then `_finish()`.

### One turn — `_take_turn`

```
with lock:      build the TurnContext          (cheap, synchronous)
without lock:   await agent.decide(context)    ← the network call
with lock:      spend budget, apply decision   (mutates state)
without lock:   emit the resulting frames
```

**The lock is never held across the model call.** That is what lets a human
inject an offer or say something while an agent is thinking — the human paths
take the same lock and land cleanly between turns.

### `_apply` — what one decision produces, in order

1. `ThoughtMessage` — always. This is the transcript.
2. `WhisperMessage` — if the agent whispered. Routed only to the recipient's
   context, but broadcast to the socket with a private flag, because the
   *audience* watching should see what the table can't.
3. `KnowledgeUpdateMessage` — if the decision carried claims.
4. Then exactly one of: `OfferMessage` + `GraphUpdateMessage` (offer made),
   the same pair (offer answered), or nothing (`pass`).

Opponent-model belief deltas are folded in but **never emitted** — they are
private to each agent and only ever appear in that agent's next prompt.

### The chair — `engine/chair.py`

Turn order is not fixed rotation. Whoever was just named or challenged answers
next. Everyone still acts exactly once per round; only the order changes. Set
`ENABLE_CHAIR=false` for strict round-robin.

It is a rule, not a model — it costs zero provider calls.

### Finish — `_finish()`

1. Settle any in-flight scribe tasks, bounded by `SCRIBE_SETTLE_TIMEOUT_SECONDS`.
2. Ask the rapporteur to summarise (one call, if affordable).
3. Emit `closing` — positions, what was agreed, what wasn't, and a final full
   state snapshot.

If the rapporteur is absent, disabled, out of budget, or throws, the closing
falls back to each party's last remark. **Ending is never optional; ending well
is the enrichment.**

---

## 4. The agents

| File | What it is |
| --- | --- |
| `agents/base.py` | The `Agent` protocol and `TurnContext`. The engine sees only this. |
| `agents/personas.py` | The fixed cast: temperament + private directive. No hidden objectives. |
| `agents/llm_agent.py` | The real thing. One Gemini call per turn, plus an optional search probe. |
| `agents/mock_agent.py` | `ScriptedAgent` (tests) and `RandomAgent` (keyless demo). |
| `agents/opponent_model.py` | Each agent's private, subjective read on the others. Never leaves the backend. |
| `agents/scribe.py` | Per-round observer. Links claims that support or contradict each other. |
| `agents/rapporteur.py` | End-of-session observer. Reports where the room landed. |

### The prompt, and why it's shaped that way

`llm_agent.py` builds two halves:

- **`system_prompt()`** — stable for the whole session, so it stays
  byte-identical across turns. Persona, the topic, the response contract.
- **`render_turn()`** — this turn's state.

The turn prompt **leads with what was said** and puts the resource ledger
underneath it, on one line. Sections with nothing in them are omitted entirely
rather than rendered as "(none)". This matters more than it sounds: when the
ledger dominated the prompt, agents narrated their moves instead of arguing,
which is the failure mode this whole app is built to avoid.

Personas describe **a way of arguing first and a way of trading second**. Written
the other way round, an agent handed only bargaining tactics has nothing to say
about the topic — which is the entire session.

### The failure ladder

```
call → validate against AgentDecision
     → on failure, retry ONCE with the error fed back into the prompt
     → on second failure, fall back to a safe `pass`
```

A turn always produces a valid decision and never raises into the loop. Three
near-misses are repaired without a retry at all (`_sanitize`): too many claims, a
wrong resource name, an unknown `target_offer_id`.

### Actions

`Action = Literal["offer", "accept", "reject", "pass"]`.

Note what is **not** an action: whispering. It is a side channel that rides
alongside whatever you do, because if whispering cost you your turn nobody would
ever do it. Claims and stance are likewise fields on the decision, not actions —
they ride the response the agent was already sending, which is why they cost zero
extra provider calls.

`pass` is the *normal* case, not the null case. It means "I spoke and moved
nothing", which is what most turns of a discussion are.

---

## 5. The two graphs

### Trust — `engine/trust_graph.py`

Directed. `weight(A → B)` is how much A trusts B, in `[0, 1]`, seeded at `0.5`.

| Event | Effect |
| --- | --- |
| A offers to B | `weight(B→A) += generosity_gain × favorability` |
| B accepts A's offer | `weight(A→B) += accept_gain × (0.5 + favorability)` |
| B rejects A's offer | `weight(A→B) -= reject_penalty` (flat) |

`favorability = amount / pool_total`. Everything clamps through one function
(`_nudge`) so there is exactly one place a weight can change.

#### Known gap: trust reacts to offers and nothing else

In a discussion where nobody trades, the graph never moves off its uniform seed.
The UI is honest about this — it says *"no trust signals yet"* rather than
letting a neutral web imply that everyone assessed each other and landed on
neutral. Those are different claims.

This is the app's one real design tension. As the resource layer gets less
central, the thing driving the trust graph gets less central with it. The fix is
to let trust react to **argument**, which is now possible because the scribe
already produces the signals as data and they already reach the frontend. Wiring
them in is additive and disturbs no offer path.

Four candidate signals, each needing a weight and a sign before anything is
built:

| Signal | Direction | Note |
| --- | --- | --- |
| your claim `supports` mine | up | already an edge kind |
| your claim `contradicts` mine | down | already an edge kind |
| you concede a point you previously attacked | up | the most interesting one, and the hardest to detect |
| you're caught contradicting *yourself* across rounds | down, from everyone | needs self-contradiction detection the scribe doesn't do yet |

The open questions are the part worth writing down, because the starvation itself
is obvious: how much should an argument signal move a weight relative to an
offer? Should a contradiction cost trust at all — disagreeing well is not bad
faith, and the prompt explicitly tells agents so. Should conceding raise the
conceder's trust *in the other party*, or the room's trust in the conceder?

### Knowledge — `engine/knowledge_graph.py`

Append-only, so a client merges by upsert and never reconciles a deletion.

Nodes are `party | claim | entity | evidence`. Parties reuse their session ids,
so the Ada in the trust graph and the Ada who made a claim are the same node.

**Who may say what** is the rule worth preserving:

- an **agent** reports its own claims (`asserts`) and what they're about
  (`about`) — it's the only party that knows what it meant
- the **engine** stamps evidence (`cites`) from searches that actually ran
- the **scribe** adds `supports` / `contradicts`, because noticing that one claim
  rebuts another from two turns ago is a cross-transcript judgement no single
  speaker can make

Keeping those lanes separate is what stops the graph becoming a place where one
model asserts things about another model's argument unchallenged.

---

## 6. The wire

Eight frame types, all `{"type": ..., "payload": {...}}`, serialized with
`by_alias=True`. Defined in `models/messages.py`.

| Frame | When | Client does |
| --- | --- | --- |
| `state` | once, on connect | replaces everything |
| `round_change` | top of each round | updates round + total |
| `thought` | every turn, and every human remark | appends to the transcript |
| `whisper` | when an agent whispers | renders as a side channel |
| `knowledge_update` | agent claims; scribe pass | upserts nodes and edges |
| `offer` | offer made or answered | merges into the ledger |
| `graph_update` | alongside every offer frame | merges edge weights |
| `closing` | once, at the end | overlays positions + final snapshot |

Only `state` and `closing` carry a full snapshot. Everything else is a delta.
`graph_update` never carries nodes — the roster comes from `state`.

`models/schemas.py` is the wire contract and `tests/test_schemas.py` pins the
exact serialized JSON, so a rename can't slip through silently. Note `from_` /
`alias="from"`: `from` is a Python keyword, but **the wire key is always `from`**.

---

## 7. The call budget

The binding constraint on this whole app is provider rate limit, not engineering
time. `engine/budget.py` enforces one rule:

> **The calls needed to reach the closing are reserved first. Optional work only
> ever spends the surplus.**

At the defaults: three agents × six rounds = 18 calls to merely finish, or 36
when a topic is set and each turn also spends a search probe. Plus ~6 for the
scribe and 1 for the rapporteur. `SESSION_CALL_BUDGET=60` therefore never binds
on a normal session — it's a ceiling on runaway.

Three rules keep it affordable, and they're worth knowing before adding a
feature:

1. **Prefer schema extensions over new calls.** An agent declaring its own claim
   inside the response it was already sending costs nothing. Extracting the same
   claim with a second call costs a turn of latency.
2. **Observers run per *round*, off the critical path.** The scribe is a
   background task fired after a round closes. A citation appearing two seconds
   late is fine; a two-second stall before every turn is not.
3. **Observers get a cheap model.** `SCRIBE_MODEL` is a small fast model so it
   doesn't compete with the agents for the same per-minute budget.

Calls are also globally serialized in `llm_client.py` with a minimum gap, because
quota is per API *key*, not per session. **More concurrent sessions means slower
rounds, not more throughput.**

---

## 8. The frontend

`VITE_BACKEND_URL` decides everything: unset → `MockNegotiationClient` (a
scripted six-round run, so the UI demos with no backend at all); set →
`LiveNegotiationClient`.

Both satisfy the same `NegotiationClient` interface, so no component knows which
one it's talking to.

| Layer | File |
| --- | --- |
| Transport + frame dispatch | `lib/negotiation/live.ts` |
| snake→camel, trust rescale, merge helpers | `lib/negotiation/adapter.ts` |
| React state | `hooks/useNegotiation.ts` |
| Panels | `components/boardroom/*` |

The trust rescale lives in exactly one place: `toSignedWeight` maps the backend's
`0..1` (neutral `0.5`) onto the renderer's `-1..1` (neutral `0`). A consequence
worth knowing — an untouched edge arrives as exactly `0`, which is how the UI
detects a graph that hasn't moved without needing an epsilon.

---

## 9. Where things live

```
backend/app/
  main.py              composition root — everything is wired here
  config.py            every setting, each with a comment on why
  api/routes.py        REST surface
  api/ws.py            the WebSocket endpoint
  engine/
    negotiation.py     the state machine — start here
    trust_graph.py     who trusts whom
    knowledge_graph.py what was argued
    chair.py           who speaks next
    budget.py          provider-call accounting
  agents/              the cast, the real agent, the observers
  models/
    schemas.py         the wire contract
    messages.py        the WS frame union
    agent_io.py        the agent↔engine contract (never sent to the frontend)
  session/store.py     session lifecycle, capacity, expiry
  ws/broadcast.py      connection manager
  speech/              transcription + opportunistic offer parsing
```

`agent_io.py` vs `schemas.py` is a distinction worth respecting: `agent_io` is
handed to Gemini as a response schema, so anything declared there becomes a field
the model is asked to invent. Provenance (`searched`, `llm_calls`) therefore
lives on the `TurnDecision` subclass, stamped server-side.

---

## 10. Working on this

**Tests run with no key, no network, and no model weights** — 380+ of them, in
about five seconds. If a change makes that untrue, the change is wrong.

```bash
cd backend && .venv/Scripts/python -m pytest -q
```

Conventions that are deliberate, not accidental:

- **Test names are sentences.** `test_the_pool_total_is_conserved_across_a_long_random_game`.
- **Comments say *why*, not *what*.** Most non-obvious constants have a sentence
  explaining the judgement behind them. Keep that up.
- **Every optional dependency degrades.** New feature needs a key? It must be
  `None`-able and the session must still finish without it.
- **Prompt changes are behavioural.** Unit tests can't catch "the arguing got
  worse". Run a live session and read it.
