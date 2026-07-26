import {
  mergeEdges,
  mergeOffer,
  toEdge,
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

const emptyState = (): NegotiationState => ({
  round: 0,
  pool: { resource: "CREDITS", total: 0 },
  agents: [],
  trustGraph: { nodes: [], edges: [] },
  offerLog: [],
  agentThoughts: [],
  revealedObjectives: null,
});

/**
 * Talks to the FastAPI backend.
 *
 * The socket is a *discriminated union* of incremental frames —
 * `{ type: "state" | "offer" | "graph_update" | "thought" | "round_change" |
 * "reveal", payload }` — not a stream of whole states. Only `state` (sent on
 * connect) and the reveal's `final_state` carry a complete snapshot; the rest
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

  constructor(private baseUrl: string) {}

  private get wsUrl() {
    return this.baseUrl.replace(/^http/, "ws").replace(/\/$/, "") + "/ws/negotiation";
  }

  connect() {
    if (this.ws) return;
    this.setStatus("connecting");
    const ws = new WebSocket(this.wsUrl);
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

      case "round_change":
        this.state = {
          ...this.state,
          round: (payload as unknown as { round: number }).round,
        };
        break;

      case "thought":
        this.state = {
          ...this.state,
          agentThoughts: [...this.state.agentThoughts, toThought(payload)],
        };
        break;

      case "offer":
        this.state = {
          ...this.state,
          offerLog: mergeOffer(this.state.offerLog, toOffer(payload)),
        };
        break;

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

      case "reveal": {
        // The reveal carries an authoritative final snapshot, so take it whole
        // rather than merging — it also fills in revealed_objectives.
        const body = payload as unknown as {
          final_state: WireState;
          revealed_objectives: Record<string, string>;
        };
        this.state = {
          ...toState(body.final_state),
          revealedObjectives: body.revealed_objectives,
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

  subscribe(
    onState: (s: NegotiationState) => void,
    onStatus: (s: ConnectionStatus) => void,
  ) {
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

  async start() {
    this.connect();
    await this.api("/api/session/start", { method: "POST" });
  }

  async reset() {
    // The backend has a dedicated reset endpoint that stops the running round
    // loop; POSTing to /start would instead begin a whole new negotiation.
    await this.api("/api/session/reset", { method: "POST" });
    this.state = emptyState();
    this.stateSubs.forEach((fn) => fn(this.state));
  }

  async injectOffer(payload: InjectOfferPayload) {
    const res = await this.api("/api/session/inject-offer", {
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

  async sendVoiceOffer(audio: Blob): Promise<VoiceOfferResult> {
    const form = new FormData();
    // The field name must be `file` — that's what the FastAPI endpoint binds.
    form.append("file", audio, "offer.webm");
    const res = await this.api("/api/session/voice-offer", { method: "POST", body: form });
    if (!res.ok) throw new Error(`voice-offer failed (${res.status})`);
    return toVoiceResult((await res.json()) as WireVoiceResult);
  }

  private setStatus(s: ConnectionStatus) {
    this.status = s;
    this.statusSubs.forEach((fn) => fn(s));
  }
}
