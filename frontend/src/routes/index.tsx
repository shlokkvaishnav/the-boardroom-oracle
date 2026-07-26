import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { Header } from "@/components/boardroom/Header";
import { TrustGraph } from "@/components/boardroom/TrustGraph";
import { ThoughtsFeed } from "@/components/boardroom/ThoughtsFeed";
import { OfferTimeline } from "@/components/boardroom/OfferTimeline";
import { VoiceModal } from "@/components/boardroom/VoiceModal";
import { RevealOverlay } from "@/components/boardroom/RevealOverlay";
import { useNegotiation } from "@/hooks/useNegotiation";

const TITLE = "Boardroom Oracle — Live Multi-Agent AI Negotiation Arena";
const DESCRIPTION =
  "Watch three AI agents negotiate a shared resource pool in real time on a living trust graph — and join the table with your voice.";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: TITLE },
      { name: "description", content: DESCRIPTION },
      { property: "og:title", content: TITLE },
      { property: "og:description", content: DESCRIPTION },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
  }),
  component: Index,
});

function Index() {
  const { state, status, start, reset, injectOffer, sendVoiceOffer } = useNegotiation();
  const [micOpen, setMicOpen] = useState(false);
  const [selected, setSelected] = useState<number | null>(null);
  const [started, setStarted] = useState(false);

  const selectedOffer = selected !== null ? state.offerLog[selected] : undefined;
  const highlight = selectedOffer
    ? { source: selectedOffer.from, target: selectedOffer.to }
    : null;

  const handleReset = () => {
    setStarted(false);
    setSelected(null);
    reset();
  };

  return (
    <main className="flex h-screen flex-col gap-3 overflow-hidden bg-background p-3">
      <Header
        round={state.round}
        resource={state.pool.resource}
        total={state.pool.total}
        status={status}
        started={started}
        onStart={() => {
          setStarted(true);
          start();
        }}
        onReset={handleReset}
        onMic={() => setMicOpen(true)}
        recording={micOpen}
      />

      <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 lg:grid-cols-[1fr_minmax(280px,30%)]">
        <section className="panel relative min-h-0 overflow-hidden rounded-lg bg-stage">
          <div className="pointer-events-none absolute left-4 top-3 z-10 font-display text-xs font-bold tracking-[0.22em] text-muted-foreground">
            TRUST GRAPH
          </div>
          <TrustGraph state={state} highlight={highlight} dimmed={false} />
          {!started && !state.revealedObjectives && (
            <div className="pointer-events-none absolute inset-0 grid place-items-center">
              <p className="font-mono text-sm text-muted-foreground">
                press <span className="text-primary">START</span> to open the floor
              </p>
            </div>
          )}
          {state.revealedObjectives && (
            <RevealOverlay
              agents={state.agents}
              objectives={state.revealedObjectives}
              offers={state.offerLog}
              total={state.pool.total}
              onReset={handleReset}
            />
          )}
        </section>

        <ThoughtsFeed thoughts={state.agentThoughts} agents={state.agents} />
      </div>

      <OfferTimeline
        offers={state.offerLog}
        agents={state.agents}
        selected={selected}
        onSelect={setSelected}
      />

      <VoiceModal
        open={micOpen}
        agents={state.agents}
        onClose={() => setMicOpen(false)}
        onTranscribe={sendVoiceOffer}
        onConfirm={(offer) => void injectOffer(offer)}
      />
    </main>
  );
}
