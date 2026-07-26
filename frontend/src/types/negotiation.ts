export interface Agent {
  id: string;
  name: string;
  persona: "Cooperator" | "Maximizer" | "TitForTat" | "Human";
  color: string;
  isHuman: boolean;
}

export interface TrustEdge {
  source: string; // agent id
  target: string; // agent id
  weight: number; // -1.0 to 1.0
  lastOfferAccepted: boolean;
}

export interface Offer {
  id: string;
  round: number;
  from: string;
  to: string;
  resource: string;
  amount: number;
  accepted: boolean | null;
  timestamp: string;
}

export interface AgentThought {
  id: string;
  agentId: string;
  text: string;
  timestamp: string;
}

export interface NegotiationState {
  status: "idle" | "running" | "revealed";
  round: number;
  pool: { resource: string; total: number };
  agents: Agent[];
  trustGraph: {
    nodes: Array<{ id: string; label: string }>;
    edges: TrustEdge[];
  };
  offerLog: Offer[];
  agentThoughts: AgentThought[];
  revealedObjectives: { [agentId: string]: { objective: string, score: number } } | null;
}
