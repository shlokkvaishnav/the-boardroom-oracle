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

export interface Offer {
  round: number;
  from: string;
  to: string;
  resource: string;
  amount: number;
  accepted: boolean | null;
  timestamp: string;
}

export interface AgentThought {
  agentId: string;
  text: string;
  timestamp: string;
}

export interface NegotiationState {
  round: number;
  pool: { resource: string; total: number };
  agents: Agent[];
  trustGraph: { nodes: TrustNode[]; edges: TrustEdge[] };
  offerLog: Offer[];
  agentThoughts: AgentThought[];
  revealedObjectives: Record<string, string> | null;
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
  start(): Promise<void>;
  reset(): Promise<void>;
  injectOffer(payload: InjectOfferPayload): Promise<void>;
  sendVoiceOffer(audio: Blob): Promise<VoiceOfferResult>;
}

export const TOTAL_ROUNDS = 6;
