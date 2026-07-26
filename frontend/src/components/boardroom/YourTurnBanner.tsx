import { Check, Mic, X } from "lucide-react";
import type { Agent, Offer } from "@/lib/negotiation/types";

/**
 * Tells you when the table is waiting on *you*.
 *
 * The state already knew an offer was sitting unanswered in your name — it was
 * just buried in the ledger, so the moment passed unnoticed and the agents
 * appeared to be talking to nobody. This makes being addressed impossible to
 * miss, and puts the mic one click away.
 */
export function YourTurnBanner({
  offers,
  agents,
  onSpeak,
  onRespond,
}: {
  offers: Offer[];
  agents: Agent[];
  onSpeak: () => void;
  onRespond: (offerId: string, accepted: boolean) => void;
}) {
  const human = agents.find((a) => a.isHuman);
  if (!human) return null;

  // Newest first: if several are open, answer the one just made.
  const waiting = [...offers].reverse().find((o) => o.to === human.id && o.accepted === null);
  if (!waiting) return null;

  const from = agents.find((a) => a.id === waiting.from);

  return (
    <div
      role="status"
      className="panel animate-slide-in flex flex-wrap items-center gap-x-3 gap-y-2 rounded-lg border border-agent-4/70 px-4 py-3"
      style={{ boxShadow: "0 0 30px -14px var(--agent-4)" }}
    >
      <span className="font-display text-xs font-bold tracking-[0.2em] text-agent-4">
        YOUR TURN
      </span>
      <span className="font-mono text-sm text-foreground/90">
        <span className="font-bold" style={{ color: from?.color }}>
          {from?.name ?? waiting.from}
        </span>{" "}
        put <span className="font-bold tabular-nums">{waiting.amount}</span> {waiting.resource} on
        the table for you — say something back.
      </span>
      <div className="ml-auto flex items-center gap-2">
        <button
          onClick={() => onRespond(waiting.offerId, true)}
          disabled={!waiting.offerId}
          className="flex items-center gap-1.5 rounded-lg border border-trust-pos px-3 py-1.5 font-mono text-xs font-bold tracking-widest text-trust-pos transition-colors hover:bg-trust-pos hover:text-background disabled:opacity-40"
        >
          <Check className="size-3.5" />
          ACCEPT
        </button>
        <button
          onClick={() => onRespond(waiting.offerId, false)}
          disabled={!waiting.offerId}
          className="flex items-center gap-1.5 rounded-lg border border-trust-neg px-3 py-1.5 font-mono text-xs font-bold tracking-widest text-trust-neg transition-colors hover:bg-trust-neg hover:text-background disabled:opacity-40"
        >
          <X className="size-3.5" />
          REJECT
        </button>
      </div>
      <button
        onClick={onSpeak}
        className="flex items-center gap-2 rounded-lg border border-agent-4 px-4 py-1.5 font-mono text-xs font-bold tracking-widest text-agent-4 transition-colors hover:bg-agent-4 hover:text-background"
      >
        <Mic className="size-3.5" />
        SPEAK
      </button>
    </div>
  );
}
