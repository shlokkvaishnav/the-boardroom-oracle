import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { Header } from "@/components/boardroom/Header";
import { TrustGraph } from "@/components/boardroom/TrustGraph";
import { ThoughtsFeed } from "@/components/boardroom/ThoughtsFeed";
import { OfferTimeline } from "@/components/boardroom/OfferTimeline";
import { VoiceModal } from "@/components/boardroom/VoiceModal";
import { TopicPrompt } from "@/components/boardroom/TopicPrompt";
import { YourTurnBanner } from "@/components/boardroom/YourTurnBanner";
import { ClosingPanel } from "@/components/boardroom/ClosingPanel";
import { useNegotiation } from "@/hooks/useNegotiation";

const TITLE = "Boardroom Oracle — Live Multi-Agent AI Negotiation Arena";
const DESCRIPTION =
  "Give three AI agents a real-world topic and watch them argue it out — taking positions, rebutting each other, and trading a shared budget on a living trust graph. Join the table with your voice.";

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
  const { state, status, start, reset, injectOffer, say, respondToOffer, transcribe } =
    useNegotiation();
  const [micOpen, setMicOpen] = useState(false);
  const [selected, setSelected] = useState<number | null>(null);
  const [started, setStarted] = useState(false);
  const [topicOpen, setTopicOpen] = useState(false);
  const [topic, setTopic] = useState<string | null>(null);

  const selectedOffer = selected !== null ? state.offerLog[selected] : undefined;
  const highlight = selectedOffer ? { source: selectedOffer.from, target: selectedOffer.to } : null;

  const handleReset = () => {
    setStarted(false);
    setSelected(null);
    setTopic(null);
    reset();
  };

  // START never opens the floor directly — it always asks what the table is
  // discussing first.
  const handleConfirmTopic = (chosen: string | null) => {
    setTopicOpen(false);
    setTopic(chosen);
    setStarted(true);
    start(chosen);
  };

  return (
    <main className="flex h-screen flex-col gap-3 overflow-hidden bg-background p-3">
      <Header
        round={state.round}
        resource={state.pool.resource}
        total={state.pool.total}
        status={status}
        started={started}
        onStart={() => setTopicOpen(true)}
        onReset={handleReset}
        onMic={() => setMicOpen(true)}
        recording={micOpen}
      />

      {(status === "at-capacity" || status === "expired") && (
        <div
          role="status"
          className="panel rounded-lg border border-trust-neg/60 px-4 py-3 font-mono text-sm text-foreground"
        >
          {status === "at-capacity" ? (
            <>
              <span className="font-bold text-trust-neg">TABLE FULL — </span>
              every seat is taken by another negotiation right now. Rounds are paced to stay inside
              a shared rate limit, so this clears on its own. Try START again in a few minutes.
            </>
          ) : (
            <>
              <span className="font-bold text-trust-neg">SESSION ENDED — </span>
              the negotiation this tab was watching has finished or timed out. Press START for a new
              one.
            </>
          )}
        </div>
      )}

      <YourTurnBanner
        offers={state.offerLog}
        agents={state.agents}
        onSpeak={() => setMicOpen(true)}
        onRespond={(id, accepted) => void respondToOffer(id, accepted)}
      />

      <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 lg:grid-cols-[1fr_minmax(280px,30%)]">
        <section className="panel relative min-h-0 overflow-hidden rounded-lg bg-stage">
          <div className="pointer-events-none absolute left-4 top-3 z-10 font-display text-xs font-bold tracking-[0.22em] text-muted-foreground">
            TRUST GRAPH
          </div>
          <TrustGraph state={state} highlight={highlight} dimmed={false} />
          {topic && (
            <div className="pointer-events-none absolute right-4 top-3 z-10 max-w-[45%] truncate font-mono text-xs text-agent-4">
              ON THE TABLE: {topic}
            </div>
          )}
          {!started && !state.closingPositions && (
            <div className="pointer-events-none absolute inset-0 grid place-items-center">
              <p className="font-mono text-sm text-muted-foreground">
                press <span className="text-primary">START</span> to open the floor
              </p>
            </div>
          )}
          {state.closingPositions && (
            <ClosingPanel
              agents={state.agents}
              positions={state.closingPositions}
              offers={state.offerLog}
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

      <TopicPrompt
        open={topicOpen}
        onCancel={() => setTopicOpen(false)}
        onConfirm={handleConfirmTopic}
        onTranscribe={transcribe}
      />

      <VoiceModal
        open={micOpen}
        agents={state.agents}
        onClose={() => setMicOpen(false)}
        onTranscribe={say}
        onConfirm={(offer) => void injectOffer(offer)}
      />
    </main>
  );
}
