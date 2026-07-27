# Four Chairs

**Give three AI agents a topic people actually disagree about, and watch them
argue it out — live, while the argument draws itself as a graph.**

<!-- ![The boardroom, mid-session](docs/images/hero.png) -->

Ada, Rex and Mira sit at a four-seat table. You take the fourth seat. Each has a
temperament rather than a script — a conciliator, an opportunist, and a strict
reciprocator — so they reach different conclusions about the same question
without anyone handing them a position to defend.

They argue for six rounds. They cite live web sources. They change their minds,
or conspicuously don't. And you can cut in at any point with your voice.

---

## What you're looking at

<!-- ![Trust graph and argument graph side by side](docs/images/graphs.png) -->

**The stage** shows one of two graphs, toggled live:

- **TRUST** — who trusts whom, as a force-directed web. Directed edges, so Ada
  trusting Rex is a different thing from Rex trusting Ada.
- **ARGUMENT** — the knowledge graph. Every claim anyone makes becomes a node,
  linked to the things it's about, the sources cited for it, and — via a scribe
  that reads each round — the other claims it supports or contradicts.

**Table talk** is the transcript, with a citation chip under any remark where the
agent actually ran a search. The source is stamped server-side, so it can't be
hallucinated.

**Who changed their mind** charts each agent's stance on the topic across the
six rounds. It is the most interesting question about any debate and it is the
one thing most multi-agent demos can't answer.

**Where they landed** closes the session: each party's settled position, what the
room agreed on, and what it didn't — written by a rapporteur that reads the whole
transcript once at the end.

---

## Taking your seat

<!-- ![Speaking into the discussion](docs/images/voice.png) -->

Press the mic and say something. It lands in the transcript and every agent sees
it on their next turn, so they answer you. It costs no round and requires no
particular form — you can just make a point, or ask the question nobody asked.

There is also a shared pool of resource on the table, split evenly at the start.
Anyone, including you, can move some of theirs to someone else — to back a
position they buy, or to pay for a concession they want. It's a stake, not the
subject: the agents are told in as many words that the pool is what the argument
is *over*, never what it's *about*.

---

## Running it

One command, and it works without an API key — it falls back to scripted agents
so the UI still demos offline.

```bash
docker compose up --build
```

Frontend on <http://localhost:3000>, API on <http://localhost:8000> (`/docs` for
the OpenAPI browser). Add a `GEMINI_API_KEY` to `backend/.env` for real agents,
and optionally a `TAVILY_API_KEY` to let them search the web and a `GROQ_API_KEY`
for fast voice transcription.

Native setup, every environment variable, and the test commands are in
**[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)**.
