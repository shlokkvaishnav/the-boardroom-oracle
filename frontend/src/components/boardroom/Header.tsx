import { Mic, Play, RotateCcw } from "lucide-react";
import { type ConnectionStatus } from "@/lib/negotiation/types";

const statusLabel: Record<ConnectionStatus, string> = {
  idle: "OFFLINE",
  connecting: "LINKING",
  open: "LIVE",
  closed: "CLOSED",
  error: "ERROR",
  "at-capacity": "TABLE FULL",
  expired: "SESSION ENDED",
};

export function Header({
  round,
  totalRounds,
  resource,
  total,
  status,
  started,
  onStart,
  onReset,
  onMic,
  recording,
}: {
  round: number;
  totalRounds: number;
  resource: string;
  total: number;
  status: ConnectionStatus;
  started: boolean;
  onStart: () => void;
  onReset: () => void;
  onMic: () => void;
  recording: boolean;
}) {
  return (
    <header className="panel grid grid-cols-[minmax(0,1fr)_auto] items-center gap-4 rounded-lg px-4 py-3 lg:flex lg:justify-between">
      <div className="flex min-w-0 items-center gap-5">
        <h1 className="font-display truncate text-xl font-bold tracking-[0.18em] text-glow text-primary lg:text-2xl">
          BOARDROOM ORACLE
        </h1>
        <span
          className={`hidden shrink-0 rounded border px-2 py-0.5 font-mono text-[10px] font-bold tracking-widest sm:inline ${
            status === "open"
              ? "border-trust-pos text-trust-pos"
              : "border-border text-muted-foreground"
          }`}
        >
          ● {statusLabel[status]}
        </span>
      </div>

      <div className="col-span-2 flex flex-wrap items-center gap-x-6 gap-y-2 lg:col-auto">
        {/* Both come from the backend, so both read as em-dashes until the
            first state frame lands rather than asserting a wrong number. */}
        <Stat label="ROUND" value={`${round || 0} / ${totalRounds || "—"}`} />
        <Stat
          label={resource ? `POOL · ${resource.toUpperCase()}` : "POOL"}
          value={total ? total.toLocaleString() : "—"}
          muted
        />
      </div>

      <div className="col-span-2 flex items-center justify-end gap-3 lg:col-auto">
        <button
          onClick={onStart}
          disabled={started}
          className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2.5 font-display text-sm font-bold tracking-wide text-primary-foreground transition-transform hover:scale-105 disabled:opacity-40 disabled:hover:scale-100"
        >
          <Play className="size-4" /> START
        </button>
        <button
          onClick={onReset}
          aria-label="Reset session"
          className="rounded-md border border-border p-2.5 text-muted-foreground transition-colors hover:border-primary hover:text-primary"
        >
          <RotateCcw className="size-4" />
        </button>
        <button
          onClick={onMic}
          aria-label="Join by voice"
          className={`grid size-12 shrink-0 place-items-center rounded-full border-2 transition-colors ${
            recording
              ? "animate-rec-pulse border-destructive bg-destructive text-destructive-foreground"
              : "border-agent-4 text-agent-4 hover:bg-agent-4/15"
          }`}
        >
          <Mic className="size-5" />
        </button>
      </div>
    </header>
  );
}

/**
 * `muted` marks a number that is context rather than headline. The pool is the
 * stake anyone can put behind a position, not the subject of the session, so it
 * should not read as a scoreboard sitting next to the round counter.
 */
function Stat({ label, value, muted }: { label: string; value: string; muted?: boolean }) {
  return (
    <div className="min-w-0">
      <p className="font-mono text-[10px] tracking-[0.2em] text-muted-foreground">{label}</p>
      <p
        className={
          muted
            ? "font-mono text-sm tabular-nums text-muted-foreground"
            : "font-display text-lg font-bold tabular-nums text-foreground lg:text-xl"
        }
      >
        {value}
      </p>
    </div>
  );
}
