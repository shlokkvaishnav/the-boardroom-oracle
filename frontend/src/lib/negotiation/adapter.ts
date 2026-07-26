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
  KnowledgeEdge,
  KnowledgeNode,
  NegotiationState,
  Offer,
  SearchRecord,
  TrustEdge,
  Whisper,
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

export interface WireSearchRecord {
  query: string;
  result_snippet: string;
  source_url: string;
}

export interface WireThought {
  agent_id: string;
  text: string;
  round?: number;
  stance?: number | null;
  timestamp: string;
  searched?: WireSearchRecord[];
}

export interface WireKnowledgeNode {
  id: string;
  kind: KnowledgeNode["kind"];
  label: string;
  round: number | null;
  author_id: string | null;
  claim_kind: string | null;
  verdict: KnowledgeNode["verdict"];
  source_url: string | null;
}

export interface WireKnowledgeGraph {
  nodes: WireKnowledgeNode[];
  edges: KnowledgeEdge[];
}

export interface WireState {
  round: number;
  total_rounds: number;
  pool: { resource: string; total: number };
  agents: WireAgent[];
  trust_graph: { nodes: Array<{ id: string; label: string }>; edges: WireEdge[] };
  // Optional so a backend that predates the knowledge graph still parses.
  knowledge_graph?: WireKnowledgeGraph;
  offer_log: WireOffer[];
  agent_thoughts: WireThought[];
  whispers?: Whisper[];
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

export const toSearchRecord = (r: WireSearchRecord): SearchRecord => ({
  query: r.query,
  // `result_snippet` on the wire; the extra word earns nothing on this side.
  snippet: r.result_snippet,
  sourceUrl: r.source_url,
});

export const toThought = (t: WireThought): AgentThought => ({
  agentId: t.agent_id,
  text: t.text,
  round: t.round ?? 0,
  stance: t.stance ?? null,
  timestamp: t.timestamp,
  searched: (t.searched ?? []).map(toSearchRecord),
});

export const toKnowledgeNode = (n: WireKnowledgeNode): KnowledgeNode => ({
  id: n.id,
  kind: n.kind,
  label: n.label,
  round: n.round,
  authorId: n.author_id,
  claimKind: n.claim_kind,
  verdict: n.verdict,
  sourceUrl: n.source_url,
});

export const toState = (s: WireState): NegotiationState => ({
  round: s.round,
  totalRounds: s.total_rounds,
  pool: s.pool,
  agents: s.agents.map(toAgent),
  trustGraph: {
    nodes: s.trust_graph.nodes,
    edges: s.trust_graph.edges.map(toEdge),
  },
  knowledgeGraph: {
    nodes: (s.knowledge_graph?.nodes ?? []).map(toKnowledgeNode),
    edges: s.knowledge_graph?.edges ?? [],
  },
  offerLog: s.offer_log.map(toOffer),
  agentThoughts: s.agent_thoughts.map(toThought),
  whispers: s.whispers ?? [],
  holdings: s.holdings ?? {},
  closingPositions: s.closing_positions,
  // Only the closing frame carries these; a mid-game snapshot has none.
  agreed: [],
  unresolved: [],
  synthesised: false,
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

/**
 * Fold a `knowledge_update` delta in.
 *
 * The graph is additive, so this is a pure upsert and never needs to reconcile
 * a deletion. The one in-place change is a claim re-sent with a filled-in
 * `verdict`, which upserting on node id handles without a special case.
 */
export function mergeKnowledge(
  current: { nodes: KnowledgeNode[]; edges: KnowledgeEdge[] },
  incoming: { nodes: KnowledgeNode[]; edges: KnowledgeEdge[] },
): { nodes: KnowledgeNode[]; edges: KnowledgeEdge[] } {
  const nodes = current.nodes.slice();
  for (const node of incoming.nodes) {
    const index = nodes.findIndex((n) => n.id === node.id);
    if (index === -1) nodes.push(node);
    else nodes[index] = node;
  }

  const edges = current.edges.slice();
  for (const edge of incoming.edges) {
    const exists = edges.some(
      (e) => e.source === edge.source && e.target === edge.target && e.kind === edge.kind,
    );
    if (!exists) edges.push(edge);
  }

  return { nodes, edges };
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
