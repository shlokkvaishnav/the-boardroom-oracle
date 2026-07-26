import { useCallback, useEffect, useMemo, useState } from "react";
import { getNegotiationClient, IS_MOCK } from "@/lib/negotiation/client";
import type {
  ConnectionStatus,
  InjectOfferPayload,
  NegotiationState,
} from "@/lib/negotiation/types";

// Everything here is a placeholder until the first `state` frame arrives. The
// pool and round count are the backend's to declare — this used to claim
// "CREDITS"/1000 against a backend defaulting to budget/100, so the header was
// briefly wrong on every load.
const initial: NegotiationState = {
  round: 0,
  totalRounds: 0,
  pool: { resource: "", total: 0 },
  agents: [],
  trustGraph: { nodes: [], edges: [] },
  knowledgeGraph: { nodes: [], edges: [] },
  agreed: [],
  unresolved: [],
  synthesised: false,
  offerLog: [],
  agentThoughts: [],
  holdings: {},
  closingPositions: null,
};

export function useNegotiation() {
  const client = useMemo(() => getNegotiationClient(), []);
  const [state, setState] = useState<NegotiationState>(initial);
  const [status, setStatus] = useState<ConnectionStatus>("idle");

  useEffect(() => client.subscribe(setState, setStatus), [client]);

  // Rejoin a negotiation this tab already owns, so a refresh mid-game picks up
  // where it left off instead of stranding a running session on the backend.
  useEffect(() => {
    void client.resume();
  }, [client]);

  const start = useCallback(
    (contextTopic?: string | null) => void client.start(contextTopic),
    [client],
  );
  const reset = useCallback(() => {
    setState(initial);
    void client.reset();
  }, [client]);
  const injectOffer = useCallback((p: InjectOfferPayload) => client.injectOffer(p), [client]);
  const sendVoiceOffer = useCallback((b: Blob) => client.sendVoiceOffer(b), [client]);
  const transcribe = useCallback((b: Blob) => client.transcribe(b), [client]);
  const say = useCallback((b: Blob) => client.say(b), [client]);
  const respondToOffer = useCallback(
    (id: string, accepted: boolean) => client.respondToOffer(id, accepted),
    [client],
  );

  return {
    state,
    status,
    start,
    reset,
    injectOffer,
    sendVoiceOffer,
    say,
    respondToOffer,
    transcribe,
    isMock: IS_MOCK,
  };
}
