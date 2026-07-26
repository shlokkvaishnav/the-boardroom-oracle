export type Persona = "Cooperator" | "Maximizer" | "TitForTat" | "Human";

export interface Agent {
  id: string;
  name: string;
  persona: Persona;
  color: string;
  isHuman: boolean;
}

export interface TrustNode {
  id: string;
  label: string;
}

export interface TrustEdge {
  source: string;
  target: string;
  /** -1 (distrust) .. 1 (trust) */
  weight: number;
  lastOfferAccepted: boolean;
}

/** What a knowledge-graph node represents. */
export type KnowledgeNodeKind = "party" | "claim" | "entity" | "evidence";

/**
 * How two nodes relate.
 *
 * `asserts` and `about` come from the speaker; `cites` is stamped by the server
 * from a search that really ran; `supports`/`contradicts` can only come from a
 * pass that read the whole round.
 */
export type KnowledgeEdgeKind = "asserts" | "about" | "cites" | "supports" | "contradicts";

/** `unchecked` is the honest default — most claims are never checked. */
export type Verdict = "unchecked" | "supported" | "unsupported" | "contradicted";

export interface KnowledgeNode {
  id: string;
  kind: KnowledgeNodeKind;
  label: string;
  /** claim only. `party` ids match the trust graph's, so the two share nodes. */
  round?: number | null;
  authorId?: string | null;
  claimKind?: string | null;
  verdict?: Verdict | null;
  /** evidence only. */
  sourceUrl?: string | null;
}

export interface KnowledgeEdge {
  source: string;
  target: string;
  kind: KnowledgeEdgeKind;
}

export interface Offer {
  round: number;
  from: string;
  to: string;
  resource: string;
  amount: number;
  accepted: boolean | null;
  timestamp: string;
  /** Handle for answering this specific offer. */
  offerId: string;
}

/**
 * Provenance for one thing an agent actually looked up.
 *
 * Server-stamped from a tool call that really ran, never model output, so it
 * cannot be hallucinated. Empty on the great majority of turns.
 */
export interface SearchRecord {
  query: string;
  snippet: string;
  sourceUrl: string;
}

export interface AgentThought {
  agentId: string;
  text: string;
  timestamp: string;
  /** Non-empty only on turns where the agent invoked `web_search`. */
  searched: SearchRecord[];
}

export interface NegotiationState {
  round: number;
  /** Session length, from the backend. Never assume six. */
  totalRounds: number;
  pool: { resource: string; total: number };
  agents: Agent[];
  trustGraph: { nodes: TrustNode[]; edges: TrustEdge[] };
  /** What was argued, as opposed to who trusts whom. Empty without a topic. */
  knowledgeGraph: { nodes: KnowledgeNode[]; edges: KnowledgeEdge[] };
  offerLog: Offer[];
  agentThoughts: AgentThought[];
  /** Current split of the pool, live. */
  holdings: Record<string, number>;
  /** Each party's closing statement on the topic. Null until the end. */
  closingPositions: Record<string, string> | null;
}

export type ConnectionStatus =
  | "idle"
  | "connecting"
  | "open"
  | "closed"
  | "error"
  /** The backend is running its maximum number of negotiations. Transient. */
  | "at-capacity"
  /** The session id we held no longer resolves — it expired or was reset. */
  | "expired";

export interface InjectOfferPayload {
  from: string;
  to: string;
  resource: string;
  amount: number;
}

export interface VoiceOfferResult {
  transcript: string;
  offer?: InjectOfferPayload;
  /** Backend's own read on whether this is safe to inject without review. */
  confidence?: "high" | "low";
}

export interface NegotiationClient {
  /** Rejoin a session already in flight, if this tab remembers one. */
  resume(): Promise<void>;
  disconnect(): void;
  subscribe(
    onState: (state: NegotiationState) => void,
    onStatus: (status: ConnectionStatus) => void,
  ): () => void;
  /**
   * Open the floor. `contextTopic` is the real-world premise every agent is
   * given, and is also what enables their `web_search` tool.
   */
  start(contextTopic?: string | null): Promise<void>;
  reset(): Promise<void>;
  injectOffer(payload: InjectOfferPayload): Promise<void>;
  /** Speak into the discussion. Any offer found is a bonus, never required. */
  say(audio: Blob): Promise<VoiceOfferResult>;
  /** Accept or reject an offer made to you. */
  respondToOffer(offerId: string, accepted: boolean): Promise<void>;
  sendVoiceOffer(audio: Blob): Promise<VoiceOfferResult>;
  /** Plain speech-to-text, with no offer parsing. Used for the spoken topic. */
  transcribe(audio: Blob): Promise<string>;
}
