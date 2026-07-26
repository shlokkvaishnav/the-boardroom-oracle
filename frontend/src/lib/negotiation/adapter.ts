/**
 * Translates the FastAPI backend's wire format into this app's view types.
 *
 * The backend implements the written spec: snake_case keys, and trust weights
 * in 0..1 where 0.5 is neutral. This app was written camelCase with weights in
 * -1..1 where 0 is neutral. Rather than bend either side, every difference is
 * handled here, in one file, so the components stay unaware of it.
 *
 * Backend reference: backend/README.md -> "Frontend integration".
 */
import type {
  Agent,
  AgentThought,
  NegotiationState,
  Offer,
  TrustEdge,
  VoiceOfferResult,
} from "./types";

/* ---------- backend shapes (exactly what FastAPI emits) ---------- */

export interface WireAgent {
  id: string;
  name: string;
  persona: string;
  color: string;
  is_human: boolean;
}

export interface WireEdge {
  source: string;
  target: string;
  weight: number;
  last_offer_accepted: boolean | null;
}

export interface WireOffer {
  round: number;
  from: string;
  to: string;
  resource: string;
  amount: number;
  accepted: boolean | null;
  timestamp: string;
  offer_id?: string;
}

export interface WireThought {
  agent_id: string;
  text: string;
  timestamp: string;
}

export interface WireState {
  round: number;
  pool: { resource: string; total: number };
  agents: WireAgent[];
  trust_graph: { nodes: Array<{ id: string; label: string }>; edges: WireEdge[] };
  offer_log: WireOffer[];
  agent_thoughts: WireThought[];
  holdings?: Record<string, number>;
  closing_positions: Record<string, string> | null;
}

export interface WireVoiceResult {
  transcript: string;
  parsed_offer: {
    from: string;
    to: string;
    resource: string;
    amount: number;
  } | null;
  confidence: "high" | "low";
}

/* ---------- conversions ---------- */

/**
 * Backend trust is 0..1 with 0.5 neutral; the graph renderer wants -1..1 with
 * 0 neutral, so distrust reads as a red repelling edge instead of a thin
 * green one.
 */
export const toSignedWeight = (weight: number) => weight * 2 - 1;

export const toAgent = (a: WireAgent): Agent => ({
  id: a.id,
  name: a.name,
  persona: a.persona as Agent["persona"],
  color: a.color,
  isHuman: a.is_human,
});

export const toEdge = (e: WireEdge): TrustEdge => ({
  source: e.source,
  target: e.target,
  weight: toSignedWeight(e.weight),
  // The backend uses null for "no offer answered yet"; the renderer only
  // distinguishes accepted from not.
  lastOfferAccepted: e.last_offer_accepted === true,
});

export const toOffer = (o: WireOffer): Offer => ({
  round: o.round,
  from: o.from,
  to: o.to,
  resource: o.resource,
  amount: o.amount,
  accepted: o.accepted,
  timestamp: o.timestamp,
  offerId: o.offer_id ?? "",
});

export const toThought = (t: WireThought): AgentThought => ({
  agentId: t.agent_id,
  text: t.text,
  timestamp: t.timestamp,
});

export const toState = (s: WireState): NegotiationState => ({
  round: s.round,
  pool: s.pool,
  agents: s.agents.map(toAgent),
  trustGraph: {
    nodes: s.trust_graph.nodes,
    edges: s.trust_graph.edges.map(toEdge),
  },
  offerLog: s.offer_log.map(toOffer),
  agentThoughts: s.agent_thoughts.map(toThought),
  holdings: s.holdings ?? {},
  closingPositions: s.closing_positions,
});

export const toVoiceResult = (r: WireVoiceResult): VoiceOfferResult => ({
  transcript: r.transcript,
  offer: r.parsed_offer ?? undefined,
  confidence: r.confidence,
});

/* ---------- incremental merges ---------- */

/**
 * An offer arrives twice: once when made (accepted: null) and again when
 * answered. The pair is identified by timestamp + endpoints, which the backend
 * preserves when it stamps the outcome — so the second frame updates the
 * existing row rather than appending a duplicate.
 */
export function mergeOffer(log: Offer[], incoming: Offer): Offer[] {
  const index = log.findIndex(
    (o) => o.timestamp === incoming.timestamp && o.from === incoming.from && o.to === incoming.to,
  );
  if (index === -1) return [...log, incoming];
  const next = log.slice();
  next[index] = incoming;
  return next;
}

export function mergeEdges(edges: TrustEdge[], incoming: TrustEdge[]): TrustEdge[] {
  const next = edges.slice();
  for (const edge of incoming) {
    const index = next.findIndex((e) => e.source === edge.source && e.target === edge.target);
    if (index === -1) next.push(edge);
    else next[index] = edge;
  }
  return next;
}
