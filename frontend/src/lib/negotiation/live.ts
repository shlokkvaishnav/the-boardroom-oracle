import {
  mergeEdges,
  mergeKnowledge,
  mergeOffer,
  toEdge,
  toKnowledgeNode,
  toOffer,
  toState,
  toThought,
  toVoiceResult,
  type WireState,
  type WireVoiceResult,
} from "./adapter";
import type {
  ConnectionStatus,
  InjectOfferPayload,
  NegotiationClient,
  NegotiationState,
  VoiceOfferResult,
} from "./types";

/**
 * Where this tab remembers its session id.
 *
 * `sessionStorage`, not `localStorage`: a refresh should rejoin the game in
 * progress, but a new tab should be a new negotiation, and closing the tab
 * should let the backend reclaim the slot rather than leaving a ghost id that
 * outlives the server's TTL.
 */
const SESSION_KEY = "boardroom-oracle:session-id";

function readStoredSessionId(): string | null {
  try {
    return globalThis.sessionStorage?.getItem(SESSION_KEY) ?? null;
  } catch {
    return null; // SSR, or storage blocked by the browser
  }
}

function storeSessionId(sessionId: string) {
  try {
    globalThis.sessionStorage?.setItem(SESSION_KEY, sessionId);
  } catch {
    /* non-fatal: the session still works, it just won't survive a refresh */
  }
}

function clearStoredSessionId() {
  try {
    globalThis.sessionStorage?.removeItem(SESSION_KEY);
  } catch {
    /* non-fatal */
  }
}

const emptyState = (): NegotiationState => ({
  round: 0,
  // Both are placeholders until the first `state` frame lands, which is why
  // nothing renders a round count or a resource name before then.
  totalRounds: 0,
  pool: { resource: "", total: 0 },
  agents: [],
  trustGraph: { nodes: [], edges: [] },
  knowledgeGraph: { nodes: [], edges: [] },
  offerLog: [],
  agreed: [],
  unresolved: [],
  synthesised: false,
  agentThoughts: [],
  whispers: [],
  holdings: {},
  closingPositions: null,
});

/**
 * Talks to the FastAPI backend.
 *
 * The socket is a *discriminated union* of incremental frames —
 * `{ type: "state" | "offer" | "graph_update" | "thought" | "round_change" |
 * "closing", payload }` — not a stream of whole states. Only `state` (sent on
 * connect) and the closing's `final_state` carry a complete snapshot; the rest
 * are deltas that must be merged. Treating any payload with a `round` field as
 * a full state would silently wipe the board every time an offer landed, since
 * offers and round changes both carry `round`.
 */
export class LiveNegotiationClient implements NegotiationClient {
  private ws: WebSocket | null = null;
  private state: NegotiationState = emptyState();
  private stateSubs = new Set<(s: NegotiationState) => void>();
  private statusSubs = new Set<(s: ConnectionStatus) => void>();
  private status: ConnectionStatus = "idle";

  private sessionId: string | null = null;

  constructor(private baseUrl: string) {}

  private wsUrl(sessionId: string) {
    return this.baseUrl.replace(/^http/, "ws").replace(/\/$/, "") + `/ws/negotiation/${sessionId}`;
  }

  private connect(sessionId: string) {
    if (this.ws) return;
    this.setStatus("connecting");
    const ws = new WebSocket(this.wsUrl(sessionId));
    this.ws = ws;
    ws.onopen = () => this.setStatus("open");
    ws.onclose = () => {
      this.ws = null;
      this.setStatus("closed");
    };
    ws.onerror = () => this.setStatus("error");
    ws.onmessage = (evt) => {
      try {
        this.applyFrame(JSON.parse(evt.data));
      } catch {
        /* ignore malformed frames */
      }
    };
  }

  private applyFrame(frame: { type?: string; payload?: unknown }) {
    if (!frame || typeof frame.type !== "string") return;
    const payload = frame.payload as never;

    switch (frame.type) {
      case "state":
        this.state = toState(payload as unknown as WireState);
        break;

      case "round_change": {
        // The frame carries `total_rounds` as well as `round`. It used to be
        // dropped here, which is why the header had to hardcode a six.
        const tick = payload as unknown as { round: number; total_rounds: number };
        this.state = {
          ...this.state,
          round: tick.round,
          totalRounds: tick.total_rounds,
        };
        break;
      }

      case "thought":
        this.state = {
          ...this.state,
          agentThoughts: [...this.state.agentThoughts, toThought(payload)],
        };
        break;

      case "whisper":
        this.state = {
          ...this.state,
          whispers: [
            ...this.state.whispers,
            payload as unknown as NegotiationState["whispers"][number],
          ],
        };
        break;

      case "offer":
        this.state = {
          ...this.state,
          offerLog: mergeOffer(this.state.offerLog, toOffer(payload)),
        };
        break;

      case "knowledge_update": {
        const body = payload as unknown as {
          nodes: Parameters<typeof toKnowledgeNode>[0][];
          edges: NegotiationState["knowledgeGraph"]["edges"];
        };
        this.state = {
          ...this.state,
          knowledgeGraph: mergeKnowledge(this.state.knowledgeGraph, {
            nodes: body.nodes.map(toKnowledgeNode),
            edges: body.edges,
          }),
        };
        break;
      }

      case "graph_update": {
        const incoming = (payload as unknown as { edges: Parameters<typeof toEdge>[0][] }).edges;
        this.state = {
          ...this.state,
          trustGraph: {
            ...this.state.trustGraph,
            edges: mergeEdges(this.state.trustGraph.edges, incoming.map(toEdge)),
          },
        };
        break;
      }

      case "closing": {
        // The closing carries an authoritative final snapshot, so take it
        // whole rather than merging — it also fills in the final positions.
        const body = payload as unknown as {
          final_state: WireState;
          positions: Record<string, string>;
          agreed?: string[];
          unresolved?: string[];
          synthesised?: boolean;
        };
        this.state = {
          ...toState(body.final_state),
          closingPositions: body.positions,
          agreed: body.agreed ?? [],
          unresolved: body.unresolved ?? [],
          synthesised: body.synthesised ?? false,
        };
        break;
      }

      default:
        return; // unknown frame type — ignore rather than corrupt state
    }

    this.stateSubs.forEach((fn) => fn(this.state));
  }

  disconnect() {
    this.ws?.close();
    this.ws = null;
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

  private api(path: string, init?: RequestInit) {
    return fetch(this.baseUrl.replace(/\/$/, "") + path, init);
  }

  /** Path scoped to the live session. Throws rather than hitting a wrong URL. */
  private scoped(suffix: string) {
    if (!this.sessionId) throw new Error("no active session — press START first");
    return `/api/session/${this.sessionId}/${suffix}`;
  }

  /**
   * Rejoin the session this tab already owns, if it still exists.
   *
   * The id lives in `sessionStorage`: it survives a refresh, so reloading
   * rejoins the game in progress rather than silently abandoning it, but it is
   * per-tab, so two tabs are two negotiations, and it dies with the tab.
   */
  async resume() {
    const remembered = readStoredSessionId();
    if (!remembered) return;

    const snapshot = await this.api(`/api/session/${remembered}/state`);
    if (!snapshot.ok) {
      // Expired, reset, or the server restarted. Say so rather than leaving a
      // dead board on screen.
      clearStoredSessionId();
      this.setStatus("expired");
      return;
    }

    this.sessionId = remembered;
    this.state = toState((await snapshot.json()) as WireState);
    this.stateSubs.forEach((fn) => fn(this.state));
    this.connect(remembered);
  }

  async start(contextTopic?: string | null) {
    const topic = contextTopic?.trim();
    const res = await this.api("/api/session/start", {
      method: "POST",
      headers: { "content-type": "application/json" },
      // Always a body now, so the backend sees `context_topic: null` rather
      // than an absent field when the user skipped the prompt.
      body: JSON.stringify({ context_topic: topic ? topic : null }),
    });

    if (res.status === 503) {
      // Every slot is taken. Not an error the user caused, and it clears on
      // its own, so it gets its own status rather than a thrown failure.
      this.setStatus("at-capacity");
      return;
    }
    if (!res.ok) throw new Error(`start failed (${res.status})`);

    const { session_id: sessionId } = (await res.json()) as { session_id: string };
    this.sessionId = sessionId;
    storeSessionId(sessionId);
    this.connect(sessionId);

    // Seed from REST, because the socket will never send us the roster.
    // `/ws/negotiation/{id}` emits a full `state` frame *only on connect*, and
    // every frame after that is a delta (`round_change`, `thought`, `offer`,
    // `graph_update`) — none of which carry `agents`. Without this the board
    // renders with no agents and no trust-graph nodes until the `closing` frame
    // lands at the very end.
    const snapshot = await this.api(this.scoped("state"));
    if (snapshot.ok) {
      this.state = toState((await snapshot.json()) as WireState);
      this.stateSubs.forEach((fn) => fn(this.state));
    }
  }

  async reset() {
    // Scoped to this session: the backend has a dedicated reset endpoint that
    // stops only this round loop. POSTing to /start would instead begin a
    // whole new negotiation, and an unscoped reset would stop other people's.
    if (this.sessionId) {
      await this.api(this.scoped("reset"), { method: "POST" });
    }
    this.disconnect();
    this.sessionId = null;
    clearStoredSessionId();
    this.state = emptyState();
    this.stateSubs.forEach((fn) => fn(this.state));
    this.setStatus("idle");
  }

  async injectOffer(payload: InjectOfferPayload) {
    const res = await this.api(this.scoped("inject-offer"), {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      // 400 carries a readable reason (unknown recipient, amount too large…).
      const detail = await res.json().catch(() => null);
      throw new Error(detail?.detail ?? `inject-offer failed (${res.status})`);
    }
  }

  async respondToOffer(offerId: string, accepted: boolean): Promise<void> {
    const res = await this.api(this.scoped("respond"), {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ offer_id: offerId, accepted }),
    });
    if (!res.ok) {
      const detail = await res.json().catch(() => null);
      throw new Error(detail?.detail ?? `respond failed (${res.status})`);
    }
  }

  async say(audio: Blob): Promise<VoiceOfferResult> {
    const form = new FormData();
    form.append("file", audio, "remark.webm");
    const res = await this.api(this.scoped("say"), { method: "POST", body: form });
    if (!res.ok) {
      const detail = await res.json().catch(() => null);
      throw new Error(detail?.detail ?? `say failed (${res.status})`);
    }
    return toVoiceResult((await res.json()) as WireVoiceResult);
  }

  async transcribe(audio: Blob): Promise<string> {
    const form = new FormData();
    form.append("file", audio, "topic.webm");
    // Not session-scoped: the topic is spoken before a session exists.
    const res = await this.api("/api/transcribe", { method: "POST", body: form });
    if (!res.ok) {
      const detail = await res.json().catch(() => null);
      throw new Error(detail?.detail ?? `transcribe failed (${res.status})`);
    }
    return ((await res.json()) as { transcript: string }).transcript;
  }

  private setStatus(s: ConnectionStatus) {
    this.status = s;
    this.statusSubs.forEach((fn) => fn(s));
  }
}
