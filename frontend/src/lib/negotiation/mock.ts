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

/**
 * Where each party ends up, for the offline run's closing panel. These are
 * positions on the matter under discussion — the live backend gets them from
 * the rapporteur, and this is the same shape.
 */
const CLOSING: Record<string, string> = {
  a: "Guarantee volume to the smaller supplier now, and eat the cost of doing it. A supplier that folds is not a saving.",
  b: "Nothing gets signed before the new mine opens. Every month we wait, the price moves our way.",
  c: "I'll match whatever the room commits to. If ATLAS carries the guarantee, I'll carry half of it.",
  human: "Watching from the floor — the operator can cut in at any point.",
};

/**
 * The scripted table talk. Every line argues the copper question rather than
 * narrating a move, because that is what the live agents are told to do — a
 * demo that narrates its own mechanics teaches the wrong thing about the app.
 */
const THOUGHTS: Record<string, string[]> = {
  a: [
    "Supply doesn't come back before 2027. Planning around a 2026 recovery is planning to be wrong.",
    "If the smaller supplier goes under waiting for us, we've saved nothing and lost a source.",
    "VERTEX, you keep saying 'wait'. Wait for what, exactly, and who pays for the waiting?",
    "A guarantee costs us less than re-qualifying a supplier from scratch. That's not generosity, it's arithmetic.",
    "ECHO's conditional works for me. I'd rather split a firm commitment than hold a vague one alone.",
    "Guarantee the volume. We can argue about the price once someone is still there to sell to us.",
  ],
  b: [
    "Prices fall the moment the new mine opens. Everything you sign before that, you overpay for.",
    "ATLAS, 2027 is a forecast, not a fact. You're pricing certainty you don't have.",
    "The smaller supplier's problem is the smaller supplier's problem. We're not the lender of last resort.",
    "Whoever moves last on supply pays the least. That's the whole game and everyone here knows it.",
    "Fine — output fell 12%. Off a record year. Say the second half or don't say the first.",
    "Hold. The mine opens, the price breaks, and we sign at a number that isn't embarrassing.",
  ],
  c: [
    "Output fell 12% year on year. That's the number I'd want anyone arguing to start from.",
    "ATLAS made a concrete case. I'll engage with it concretely: what volume, over what term?",
    "VERTEX, you dodged the question about who absorbs the shock. I'll ask it once more.",
    "A supply guarantee is worth more than a price cut. Cheap copper you can't get isn't cheap.",
    "I'll mirror what I'm given. ATLAS argued in good faith, so I'll move toward ATLAS.",
    "Match ATLAS on the guarantee, split the exposure. Say plainly that's what I'm doing.",
  ],
  human: [
    "The operator cuts in from the floor.",
    "The operator puts something on the table mid-round.",
  ],
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
    // Matches the backend's own defaults, so the offline demo and a live
    // session put the same number in the header.
    pool: { resource: "budget", total: 100 },
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
    whispers: [],
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

  /** Speech goes in, a remark comes out. No offer is parsed out of it — the
   *  offline run has nothing to parse against, and most remarks aren't offers. */
  async say(_audio: Blob): Promise<VoiceOfferResult> {
    await new Promise((r) => setTimeout(r, 900));
    return {
      transcript: "I don't think we can wait for the mine. What happens if it slips a year?",
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
          this.pushThought(from, THOUGHTS[from][(r - 1) % THOUGHTS[from].length], r);
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
      const positions: Record<string, string> = {};
      this.state.agents.forEach((a) => {
        positions[a.id] = CLOSING[a.id] ?? CLOSING.human;
      });
      this.state = {
        ...this.state,
        closingPositions: positions,
        agreed: ["Copper supply is tighter than last year's planning assumed."],
        unresolved: [
          "Whether to guarantee volume before the new mine opens.",
          "Who absorbs the cost if the smaller supplier fails.",
        ],
        synthesised: true,
      };
      this.emit();
      this.running = false;
    });
  }

  // Sized against a pool of 100, so the ledger reads like the live backend's.
  private amountFor(from: string, round: number, i: number) {
    const base = { a: 9, b: 4, c: 7 }[from] ?? 6;
    return Math.round(base + round + i);
  }

  private acceptFor(from: string, to: string, round: number, amount: number) {
    if (from === "b") return round % 2 === 0;
    if (to === "b") return amount > 9;
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

  /** Anchored per agent and nudged each round, so the chart shows movement
   *  rather than noise. Mirrors what the backend's mock agents do. */
  private stanceFor(agentId: string, round: number): number {
    const anchor: Record<string, number> = { a: 0.4, b: -0.7, c: 0.0 };
    const base = anchor[agentId] ?? 0;
    const drift = (round / TOTAL_ROUNDS) * 0.5 * (base < 0 ? 1 : -1);
    return Math.max(-1, Math.min(1, Number((base + drift).toFixed(2))));
  }

  private pushThought(agentId: string, text: string, round = 0) {
    this.state = {
      ...this.state,
      agentThoughts: [
        ...this.state.agentThoughts,
        // The offline script never searches, so provenance is always empty.
        {
          agentId,
          text,
          round,
          stance: round > 0 ? this.stanceFor(agentId, round) : null,
          timestamp: now(),
          searched: [],
        },
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
