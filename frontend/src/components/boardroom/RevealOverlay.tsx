import type { Agent, Offer } from "@/lib/negotiation/types";

function score(agentId: string, offers: Offer[], total: number) {
  const gained = offers
    .filter((o) => o.to === agentId && o.accepted)
    .reduce((s, o) => s + o.amount, 0);
  const given = offers
    .filter((o) => o.from === agentId && o.accepted)
    .reduce((s, o) => s + o.amount, 0);
  const net = gained - given;
  return {
    net,
    pct: Math.max(0, Math.min(100, Math.round(((net + total / 2) / total) * 100))),
  };
}

export function RevealOverlay({
  agents,
  objectives,
  offers,
  total,
  onReset,
}: {
  agents: Agent[];
  objectives: Record<string, string>;
  offers: Offer[];
  total: number;
  onReset: () => void;
}) {
  return (
    <div className="absolute inset-0 z-20 flex flex-col items-center justify-center gap-6 bg-stage/80 p-6 backdrop-blur-[3px]">
      <h2 className="font-display animate-slide-in text-2xl font-bold tracking-[0.3em] text-glow text-primary">
        OBJECTIVES REVEALED
      </h2>
      <div className="grid w-full max-w-5xl gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {agents.map((a, i) => {
          const s = score(a.id, offers, total);
          return (
            <article
              key={a.id}
              className="animate-flip-in rounded-lg border p-4"
              style={{
                animationDelay: `${i * 130}ms`,
                borderColor: a.color,
                background: "color-mix(in oklab, var(--card) 90%, transparent)",
                boxShadow: `0 0 34px -12px ${a.color}`,
              }}
            >
              <div className="flex items-baseline justify-between">
                <h3
                  className="font-display text-lg font-bold tracking-wide text-glow"
                  style={{ color: a.color }}
                >
                  {a.name}
                </h3>
                <span className="font-mono text-[11px] uppercase text-muted-foreground">
                  {a.persona}
                </span>
              </div>
              <p className="mt-2 font-mono text-[13px] leading-snug text-foreground/85">
                {objectives[a.id]}
              </p>
              <div className="mt-4">
                <div className="flex justify-between font-mono text-xs text-muted-foreground">
                  <span>ACHIEVEMENT</span>
                  <span className="tabular-nums text-foreground">{s.pct}%</span>
                </div>
                <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-secondary">
                  <div
                    className="h-full rounded-full transition-[width] duration-1000"
                    style={{ width: `${s.pct}%`, background: a.color }}
                  />
                </div>
                <p className="mt-2 font-mono text-[11px] text-muted-foreground">
                  net {s.net >= 0 ? "+" : ""}
                  {s.net} credits
                </p>
              </div>
            </article>
          );
        })}
      </div>
      <button
        onClick={onReset}
        className="rounded-md border border-primary px-6 py-2.5 font-display text-sm font-bold tracking-wide text-primary transition-colors hover:bg-primary hover:text-primary-foreground"
      >
        RUN AGAIN
      </button>
    </div>
  );
}
