import { useCallback, useEffect, useMemo, useState } from "react";
import { getNegotiationClient, IS_MOCK } from "@/lib/negotiation/client";
import type {
  ConnectionStatus,
  InjectOfferPayload,
  NegotiationState,
} from "@/lib/negotiation/types";

const initial: NegotiationState = {
  round: 0,
  pool: { resource: "CREDITS", total: 1000 },
  agents: [],
  trustGraph: { nodes: [], edges: [] },
  offerLog: [],
  agentThoughts: [],
  revealedObjectives: null,
};

export function useNegotiation() {
  const client = useMemo(() => getNegotiationClient(), []);
  const [state, setState] = useState<NegotiationState>(initial);
  const [status, setStatus] = useState<ConnectionStatus>("idle");

  useEffect(() => client.subscribe(setState, setStatus), [client]);

  const start = useCallback(() => void client.start(), [client]);
  const reset = useCallback(() => {
    setState(initial);
    void client.reset();
  }, [client]);
  const injectOffer = useCallback(
    (p: InjectOfferPayload) => client.injectOffer(p),
    [client],
  );
  const sendVoiceOffer = useCallback((b: Blob) => client.sendVoiceOffer(b), [client]);

  return { state, status, start, reset, injectOffer, sendVoiceOffer, isMock: IS_MOCK };
}
