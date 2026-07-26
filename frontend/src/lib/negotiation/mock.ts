import {
  type Agent,
  type ConnectionStatus,
  type InjectOfferPayload,
  type NegotiationClient,
  type NegotiationState,
  type Offer,
  type VoiceOfferResult,
} from "./types";

/** The scripted run's own length. The live client reads this off the wire. */
const TOTAL_ROUNDS = 6;

export const AGENT_PALETTE = [
  "var(--agent-1)",
  "var(--agent-2)",
  "var(--agent-3)",
  "var(--agent-4)",
];

const AGENTS: Agent[] = [
  { id: "a", name: "ATLAS", persona: "Cooperator", color: "var(--agent-1)", isHuman: false },
  { id: "b", name: "VERTEX", persona: "Maximizer", color: "var(--agent-2)", isHuman: false },
  { id: "c", name: "ECHO", persona: "TitForTat", color: "var(--agent-3)", isHuman: false },
];

const HUMAN: Agent = {
  id: "human",
  name: "OPERATOR",
  persona: "Human",
  color: "var(--agent-4)",
  isHuman: true,
};

const OBJECTIVES: Record<string, string> = {
  a: "Maintain positive trust with every counterparty while securing at least 25% of the pool.",
  b: "Maximize personal share above 45% regardless of relationship cost.",
  c: "Mirror every counterparty: reward concessions, punish extraction.",
  human: "Disrupt the equilibrium and claim a stake through live intervention.",
};

const THOUGHTS: Record<string, string[]> = {
  a: [
    "Opening generous. Goodwill compounds faster than credits.",
    "VERTEX is anchoring high. I'll concede 5% to keep the alliance intact.",
    "ECHO mirrors me — a fair offer now buys a fair offer next round.",
    "Holding the coalition together is worth more than 40 credits.",
    "If VERTEX defects again I lose leverage. Signalling patience once more.",
    "Closing position: stable trust, adequate share. Acceptable.",
  ],
  b: [
    "Anchor at 55%. Everything after this is a discount from my number.",
    "ATLAS folds under pressure. Push again.",
    "ECHO retaliated — expensive. Recalibrating by 8 credits.",
    "Trust is a rounding error if I hold the largest block.",
    "One concession, precisely sized, to avoid a two-on-one bloc.",
    "Final extraction attempt before the ledger closes.",
  ],
  c: [
    "Baseline set. I return exactly what I receive.",
    "ATLAS was fair. Matching their generosity.",
    "VERTEX extracted from me. Rejecting their next offer on principle.",
    "Punishment delivered. Resetting to neutral.",
    "Cooperation restored with ATLAS. Trust edge strengthening.",
    "Ledger balanced. My mirror held.",
  ],
  human: ["Live offer injected from the floor.", "The operator is rewriting the board mid-round."],
};

/**
 * Scripted claims for the offline run, so the knowledge graph has something to
 * draw without a backend. All three parties argue about the same two entities,
 * which is what makes the graph show its point: claims converging on a shared
 * node rather than three disconnected islands.
 */
const CLAIMS: Record<string, Array<{ text: string; kind: string; about: string }>> = {
  a: [
    { text: "Copper supply won't recover before 2027.", kind: "fact", about: "copper" },
    { text: "The smaller supplier absorbs the whole shock.", kind: "value", about: "supply" },
  ],
  b: [
    { text: "Copper prices fall once the new mine opens.", kind: "prediction", about: "copper" },
    { text: "Whoever moves last on supply pays for it.", kind: "prediction", about: "supply" },
  ],
  c: [
    { text: "Copper output fell 12% year on year.", kind: "fact", about: "copper" },
    { text: "Supply guarantees are worth more than price cuts.", kind: "value", about: "supply" },
  ],
};

const now = () => new Date().toISOString();
const clamp = (n: number, lo = -1, hi = 1) => Math.max(lo, Math.min(hi, n));

function emptyState(): NegotiationState {
  return {
    round: 0,
    totalRounds: TOTAL_ROUNDS,
    pool: { resource: "CREDITS", total: 1000 },
    agents: [...AGENTS],
    trustGraph: {
      nodes: AGENTS.map((a) => ({ id: a.id, label: a.name })),
      edges: [],
    },
    knowledgeGraph: {
      // Parties are seeded, exactly as the backend seeds them, so the graph has
      // something to hang claims off from the first round.
      nodes: AGENTS.map((a) => ({ id: a.id, kind: "party" as const, label: a.name })),
      edges: [],
    },
    offerLog: [],
    agentThoughts: [],
    holdings: {},
    closingPositions: null,
    agreed: [],
    unresolved: [],
    synthesised: false,
  };
}

/** Deterministic-ish scripted negotiation used when no backend URL is set. */
export class MockNegotiationClient implements NegotiationClient {
  private state: NegotiationState = emptyState();
  private stateSubs = new Set<(s: NegotiationState) => void>();
  private statusSubs = new Set<(s: ConnectionStatus) => void>();
  private status: ConnectionStatus = "idle";
  private timers: ReturnType<typeof setTimeout>[] = [];
  private running = false;

  private connect() {
    this.setStatus("connecting");
    this.after(400, () => this.setStatus("open"));
  }

  /** No sessions to rejoin offline — the scripted run always starts fresh. */
  async resume() {}

  disconnect() {
    this.clearTimers();
    this.running = false;
    this.setStatus("closed");
  }

  subscribe(onState: (s: NegotiationState) => void, onStatus: (s: ConnectionStatus) => void) {
    this.stateSubs.add(onState);
    this.statusSubs.add(onStatus);
    onState(this.state);
    onStatus(this.status);
    return () => {
      this.stateSubs.delete(onState);
      this.statusSubs.delete(onStatus);
    };
  }

  async transcribe(_audio: Blob): Promise<string> {
    // No backend offline, so no speech-to-text. A canned topic keeps the mic
    // button honest about what it does rather than silently doing nothing.
    return "the 2026 copper supply squeeze";
  }

  async start(_contextTopic?: string | null) {
    // The scripted run is fixed, so the topic can't steer it. Accepted rather
    // than rejected so the offline demo takes the same path through the UI.
    if (this.running) return;
    this.running = true;
    if (this.status !== "open") this.connect();
    this.schedule();
  }

  async reset() {
    this.clearTimers();
    this.running = false;
    this.state = emptyState();
    this.emit();
    this.setStatus("open");
  }

  async injectOffer(payload: InjectOfferPayload) {
    this.ensureHuman();
    const offer: Offer = {
      round: Math.max(1, this.state.round),
      from: payload.from,
      to: payload.to,
      resource: payload.resource,
      amount: payload.amount,
      accepted: null,
      timestamp: now(),
      offerId: "",
    };
    this.push(offer);
    this.pushThought("human", THOUGHTS.human[0]);
    this.after(1600, () => {
      const accepted = payload.amount <= this.state.pool.total * 0.35;
      this.resolve(offer, accepted);
      this.pushThought(
        payload.to,
        accepted
          ? "The operator's terms are survivable. Accepted."
          : "The operator is overreaching. Declined.",
      );
    });
  }

  async respondToOffer(_offerId: string, _accepted: boolean): Promise<void> {}

  async say(audio: Blob): Promise<VoiceOfferResult> {
    return this.sendVoiceOffer(audio);
  }

  async sendVoiceOffer(_audio: Blob): Promise<VoiceOfferResult> {
    await new Promise((r) => setTimeout(r, 900));
    const target = AGENTS[Math.floor(Math.random() * AGENTS.length)];
    const amount = [120, 180, 240, 300][Math.floor(Math.random() * 4)];
    return {
      transcript: `I'll offer ${target.name} ${amount} credits for their support this round.`,
      offer: { from: "human", to: target.id, resource: "CREDITS", amount },
      confidence: "high",
    };
  }

  // ---- internals -------------------------------------------------------

  private ensureHuman() {
    if (this.state.agents.some((a) => a.isHuman)) return;
    this.state = {
      ...this.state,
      agents: [...this.state.agents, HUMAN],
      trustGraph: {
        ...this.state.trustGraph,
        nodes: [...this.state.trustGraph.nodes, { id: HUMAN.id, label: HUMAN.name }],
      },
    };
    this.emit();
  }

  private schedule() {
    const roundMs = 7000;
    for (let r = 1; r <= TOTAL_ROUNDS; r++) {
      const base = (r - 1) * roundMs;
      this.after(base, () => {
        this.state = { ...this.state, round: r };
        this.emit();
      });

      const pairs: [string, string][] = [
        ["a", "b"],
        ["b", "c"],
        ["c", "a"],
      ];
      pairs.forEach(([from, to], i) => {
        const at = base + 600 + i * 1900;
        this.after(at, () => {
          this.pushThought(from, THOUGHTS[from][(r - 1) % THOUGHTS[from].length]);
          this.pushClaim(from, r);
          const amount = this.amountFor(from, r, i);
          const offer: Offer = {
            round: r,
            from,
            to,
            resource: this.state.pool.resource,
            amount,
            accepted: null,
            timestamp: now(),
            offerId: "",
          };
          this.push(offer);
          this.after(900, () => {
            const accepted = this.acceptFor(from, to, r, amount);
            this.resolve(offer, accepted);
          });
        });
      });
    }

    this.after(TOTAL_ROUNDS * roundMs + 1200, () => {
      const revealed: Record<string, string> = {};
      this.state.agents.forEach((a) => {
        revealed[a.id] = OBJECTIVES[a.id] ?? OBJECTIVES.human;
      });
      this.state = { ...this.state, closingPositions: revealed };
      this.emit();
      this.running = false;
    });
  }

  private amountFor(from: string, round: number, i: number) {
    const base = { a: 160, b: 90, c: 130 }[from] ?? 120;
    return Math.round(base + round * 12 + i * 7);
  }

  private acceptFor(from: string, to: string, round: number, amount: number) {
    if (from === "b") return round % 2 === 0;
    if (to === "b") return amount > 160;
    return round !== 3;
  }

  private push(offer: Offer) {
    this.state = { ...this.state, offerLog: [...this.state.offerLog, offer] };
    this.emit();
  }

  private resolve(offer: Offer, accepted: boolean) {
    const offerLog = this.state.offerLog.map((o) =>
      o.timestamp === offer.timestamp && o.from === offer.from && o.to === offer.to
        ? { ...o, accepted }
        : o,
    );
    const edges = [...this.state.trustGraph.edges];
    const idx = edges.findIndex((e) => e.source === offer.from && e.target === offer.to);
    const delta = accepted ? 0.28 : -0.34;
    if (idx >= 0) {
      edges[idx] = {
        ...edges[idx],
        weight: clamp(edges[idx].weight + delta),
        lastOfferAccepted: accepted,
      };
    } else {
      edges.push({
        source: offer.from,
        target: offer.to,
        weight: clamp(delta),
        lastOfferAccepted: accepted,
      });
    }
    this.state = {
      ...this.state,
      offerLog,
      trustGraph: { ...this.state.trustGraph, edges },
    };
    this.emit();
  }

  private pushThought(agentId: string, text: string) {
    this.state = {
      ...this.state,
      agentThoughts: [
        ...this.state.agentThoughts,
        // The offline script never searches, so provenance is always empty.
        { agentId, text, timestamp: now(), searched: [] },
      ],
    };
    this.emit();
  }

  private claimSeq = 0;

  /** Mirrors the backend's rule: a claim node, its author, and what it is about. */
  private pushClaim(agentId: string, round: number) {
    const script = CLAIMS[agentId];
    if (!script) return;
    const claim = script[(round - 1) % script.length];
    const claimId = `c${++this.claimSeq}`;
    const entityId = `e:${claim.about}`;
    const graph = this.state.knowledgeGraph;

    const nodes = [...graph.nodes];
    nodes.push({
      id: claimId,
      kind: "claim",
      label: claim.text,
      round,
      authorId: agentId,
      claimKind: claim.kind,
      verdict: "unchecked",
    });
    // Entities merge on their key, exactly as they do server-side.
    if (!nodes.some((n) => n.id === entityId)) {
      nodes.push({ id: entityId, kind: "entity", label: claim.about });
    }

    this.state = {
      ...this.state,
      knowledgeGraph: {
        nodes,
        edges: [
          ...graph.edges,
          { source: agentId, target: claimId, kind: "asserts" },
          { source: claimId, target: entityId, kind: "about" },
        ],
      },
    };
    this.emit();
  }

  private after(ms: number, fn: () => void) {
    this.timers.push(setTimeout(fn, ms));
  }

  private clearTimers() {
    this.timers.forEach(clearTimeout);
    this.timers = [];
  }

  private setStatus(s: ConnectionStatus) {
    this.status = s;
    this.statusSubs.forEach((fn) => fn(s));
  }

  private emit() {
    this.stateSubs.forEach((fn) => fn(this.state));
  }
}
