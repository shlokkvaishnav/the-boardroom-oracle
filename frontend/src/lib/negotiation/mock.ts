import {
  TOTAL_ROUNDS,
  type Agent,
  type ConnectionStatus,
  type InjectOfferPayload,
  type NegotiationClient,
  type NegotiationState,
  type Offer,
  type VoiceOfferResult,
} from "./types";

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

const now = () => new Date().toISOString();
const clamp = (n: number, lo = -1, hi = 1) => Math.max(lo, Math.min(hi, n));

function emptyState(): NegotiationState {
  return {
    round: 0,
    pool: { resource: "CREDITS", total: 1000 },
    agents: [...AGENTS],
    trustGraph: {
      nodes: AGENTS.map((a) => ({ id: a.id, label: a.name })),
      edges: [],
    },
    offerLog: [],
    agentThoughts: [],
    revealedObjectives: null,
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

  async start() {
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
          const amount = this.amountFor(from, r, i);
          const offer: Offer = {
            round: r,
            from,
            to,
            resource: this.state.pool.resource,
            amount,
            accepted: null,
            timestamp: now(),
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
      this.state = { ...this.state, revealedObjectives: revealed };
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
      agentThoughts: [...this.state.agentThoughts, { agentId, text, timestamp: now() }],
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
