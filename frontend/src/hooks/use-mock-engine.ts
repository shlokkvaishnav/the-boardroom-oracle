import { useState, useEffect, useRef, useCallback } from 'react';
import { Agent, NegotiationState, Offer, AgentThought, TrustEdge } from '../types/negotiation';

const INITIAL_AGENTS: Agent[] = [
  { id: 'aria', name: 'ARIA', persona: 'Cooperator', color: '#00d4ff', isHuman: false },
  { id: 'maximus', name: 'MAXIMUS', persona: 'Maximizer', color: '#ff006e', isHuman: false },
  { id: 'cipher', name: 'CIPHER', persona: 'TitForTat', color: '#00ff9d', isHuman: false },
];

const INITIAL_EDGES: TrustEdge[] = [
  { source: 'aria', target: 'maximus', weight: 0.1, lastOfferAccepted: true },
  { source: 'aria', target: 'cipher', weight: 0.2, lastOfferAccepted: true },
  { source: 'maximus', target: 'cipher', weight: -0.1, lastOfferAccepted: false },
];

const generateId = () => Math.random().toString(36).substring(2, 9);

const SCRIPTED_EVENTS = [
  {
    round: 1,
    thoughts: [
      { agentId: 'aria', text: 'Initializing cooperative strategy. Proposing equitable split.' },
      { agentId: 'maximus', text: 'Scanning for weakness. The cooperator is vulnerable.' },
      { agentId: 'cipher', text: 'Observing initial moves. Will mirror behavior.' },
    ],
    offer: { from: 'aria', to: 'maximus', amount: 150, accepted: true },
    edgesUpdate: [{ source: 'aria', target: 'maximus', weight: 0.3, lastOfferAccepted: true }]
  },
  {
    round: 2,
    thoughts: [
      { agentId: 'maximus', text: 'Accepting naive offer. Preparing aggressive counter.' },
      { agentId: 'aria', text: 'Trust increasing with MAXIMUS. Will extend to CIPHER.' },
      { agentId: 'cipher', text: 'MAXIMUS accepted. Updating trust threshold.' },
    ],
    offer: { from: 'maximus', to: 'aria', amount: 300, accepted: false },
    edgesUpdate: [{ source: 'maximus', target: 'aria', weight: -0.2, lastOfferAccepted: false }]
  },
  {
    round: 3,
    thoughts: [
      { agentId: 'aria', text: 'Unreasonable demand from MAXIMUS. Rejecting.' },
      { agentId: 'cipher', text: 'MAXIMUS shows predatory behavior. Adjusting parameters.' },
    ],
    offer: { from: 'cipher', to: 'aria', amount: 100, accepted: true },
    edgesUpdate: [{ source: 'cipher', target: 'aria', weight: 0.5, lastOfferAccepted: true }, { source: 'cipher', target: 'maximus', weight: -0.4, lastOfferAccepted: false }]
  },
  {
    round: 4,
    thoughts: [
      { agentId: 'maximus', text: 'Alliance detected between ARIA and CIPHER. Must disrupt.' },
      { agentId: 'aria', text: 'Forming stable coalition with CIPHER.' },
    ],
    offer: { from: 'maximus', to: 'cipher', amount: 250, accepted: false },
    edgesUpdate: [{ source: 'maximus', target: 'cipher', weight: -0.6, lastOfferAccepted: false }]
  },
  {
    round: 5,
    thoughts: [
      { agentId: 'cipher', text: 'Defecting against MAXIMUS in retaliation.' },
      { agentId: 'maximus', text: 'Isolating... recalibrating resource acquisition plan.' },
    ],
    offer: { from: 'aria', to: 'cipher', amount: 200, accepted: true },
    edgesUpdate: [{ source: 'aria', target: 'cipher', weight: 0.8, lastOfferAccepted: true }]
  },
  {
    round: 6,
    thoughts: [
      { agentId: 'maximus', text: 'Executing desperation protocol. All-in maneuver.' },
    ],
    offer: { from: 'maximus', to: 'aria', amount: 400, accepted: false },
    edgesUpdate: [{ source: 'maximus', target: 'aria', weight: -0.8, lastOfferAccepted: false }]
  },
  {
    round: 7,
    thoughts: [
      { agentId: 'aria', text: 'Rejecting hostile bid. Securing remaining reserves.' },
      { agentId: 'cipher', text: 'Endgame sequence initiated. Finalizing ledger.' },
    ],
    offer: { from: 'cipher', to: 'maximus', amount: 50, accepted: true },
    edgesUpdate: [{ source: 'cipher', target: 'maximus', weight: -0.2, lastOfferAccepted: true }]
  },
  {
    round: 8,
    thoughts: [
      { agentId: 'maximus', text: 'Accepting marginal offer. Computation complete.' },
      { agentId: 'aria', text: 'Negotiation matrix resolved.' },
    ],
    offer: { from: 'aria', to: 'cipher', amount: 150, accepted: true },
    edgesUpdate: [{ source: 'aria', target: 'cipher', weight: 1.0, lastOfferAccepted: true }]
  }
];

export function useMockEngine() {
  const [state, setState] = useState<NegotiationState>({
    status: 'idle',
    round: 0,
    pool: { resource: 'Uranium-235', total: 1000 },
    agents: INITIAL_AGENTS,
    trustGraph: {
      nodes: INITIAL_AGENTS.map(a => ({ id: a.id, label: a.name })),
      edges: INITIAL_EDGES,
    },
    offerLog: [],
    agentThoughts: [],
    revealedObjectives: null
  });

  const timerRef = useRef<NodeJS.Timeout | null>(null);

  const applyEvent = useCallback((event: typeof SCRIPTED_EVENTS[0]) => {
    setState(prev => {
      const newThoughts = event.thoughts.map(t => ({
        ...t,
        id: generateId(),
        timestamp: new Date().toISOString()
      }));

      const newOffer: Offer = {
        id: generateId(),
        round: event.round,
        from: event.offer.from,
        to: event.offer.to,
        resource: prev.pool.resource,
        amount: event.offer.amount,
        accepted: event.offer.accepted,
        timestamp: new Date().toISOString()
      };

      // Merge edge updates
      let updatedEdges = [...prev.trustGraph.edges];
      event.edgesUpdate.forEach(eu => {
        const idx = updatedEdges.findIndex(e => 
          (e.source === eu.source && e.target === eu.target) || 
          (e.source === eu.target && e.target === eu.source)
        );
        if (idx >= 0) {
          updatedEdges[idx] = { ...updatedEdges[idx], weight: eu.weight, lastOfferAccepted: eu.lastOfferAccepted };
        } else {
          updatedEdges.push(eu as TrustEdge);
        }
      });

      return {
        ...prev,
        round: event.round,
        trustGraph: {
          ...prev.trustGraph,
          edges: updatedEdges,
        },
        offerLog: [...prev.offerLog, newOffer],
        agentThoughts: [...prev.agentThoughts, ...newThoughts]
      };
    });
  }, []);

  const triggerReveal = useCallback(() => {
    setState(prev => ({
      ...prev,
      status: 'revealed',
      revealedObjectives: {
        'aria': { objective: 'Maximize collective survival', score: 95 },
        'maximus': { objective: 'Monopolize 80% of resources', score: 12 },
        'cipher': { objective: 'Ensure MAXIMUS fails', score: 100 },
        ...(prev.agents.find(a => a.id === 'human') ? { 'human': { objective: 'Extract maximum value', score: 45 } } : {})
      }
    }));
  }, []);

  const advanceSimulation = useCallback(() => {
    setState(prev => {
      if (prev.round >= 8 || prev.status === 'revealed') {
        if (timerRef.current) clearInterval(timerRef.current);
        if (prev.status !== 'revealed') {
          setTimeout(triggerReveal, 1500); // Small delay before reveal
        }
        return prev;
      }
      
      const nextRound = prev.round + 1;
      const event = SCRIPTED_EVENTS.find(e => e.round === nextRound);
      if (event) {
        setTimeout(() => applyEvent(event), 0);
      }
      return prev;
    });
  }, [applyEvent, triggerReveal]);

  const start = useCallback(() => {
    if (timerRef.current) clearInterval(timerRef.current);
    setState(prev => ({ ...prev, status: 'running', round: 0 }));
    
    // Play first round immediately, then tick every 4 seconds
    setTimeout(() => advanceSimulation(), 100);
    timerRef.current = setInterval(advanceSimulation, 4000);
  }, [advanceSimulation]);

  const reset = useCallback(() => {
    if (timerRef.current) clearInterval(timerRef.current);
    setState({
      status: 'idle',
      round: 0,
      pool: { resource: 'Uranium-235', total: 1000 },
      agents: INITIAL_AGENTS,
      trustGraph: {
        nodes: INITIAL_AGENTS.map(a => ({ id: a.id, label: a.name })),
        edges: INITIAL_EDGES,
      },
      offerLog: [],
      agentThoughts: [],
      revealedObjectives: null
    });
  }, []);

  const injectHumanOffer = useCallback((amount: number, to: string) => {
    setState(prev => {
      let agents = prev.agents;
      let edges = prev.trustGraph.edges;
      let nodes = prev.trustGraph.nodes;

      if (!agents.find(a => a.id === 'human')) {
        const humanAgent: Agent = {
          id: 'human', name: 'HUMAN', persona: 'Human', color: '#ffb703', isHuman: true
        };
        agents = [...agents, humanAgent];
        nodes = [...nodes, { id: 'human', label: 'HUMAN' }];
        // Initialize edges for human
        INITIAL_AGENTS.forEach(a => {
          edges.push({ source: 'human', target: a.id, weight: 0, lastOfferAccepted: false });
        });
      }

      const newOffer: Offer = {
        id: generateId(),
        round: prev.round,
        from: 'human',
        to,
        resource: prev.pool.resource,
        amount,
        accepted: null, // Pending
        timestamp: new Date().toISOString()
      };

      const newThought: AgentThought = {
        id: generateId(),
        agentId: 'human',
        text: `Injecting manual offer to ${to}: ${amount} units.`,
        timestamp: new Date().toISOString()
      };

      return {
        ...prev,
        agents,
        trustGraph: { nodes, edges },
        offerLog: [...prev.offerLog, newOffer],
        agentThoughts: [...prev.agentThoughts, newThought]
      };
    });
  }, []);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, []);

  return { state, start, reset, injectHumanOffer };
}
