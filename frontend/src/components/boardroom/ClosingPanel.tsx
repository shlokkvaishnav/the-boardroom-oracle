import type { Agent, Offer } from "@/lib/negotiation/types";

/** Net credits moved, purely as a footnote — the argument is the point. */
function net(agentId: string, offers: Offer[]) {
  const gained = offers
    .filter((o) => o.to === agentId && o.accepted)
    .reduce((s, o) => s + o.amount, 0);
  const given = offers
    .filter((o) => o.from === agentId && o.accepted)
    .reduce((s, o) => s + o.amount, 0);
  return gained - given;
}

/**
 * Where everyone finished standing.
 *
 * Replaces the old reveal overlay. There are no hidden objectives to unmask and
 * no score to award, so this shows each party's closing statement — the last
 * thing they actually said — rather than grading them against a secret goal.
 */
export function ClosingPanel({
  agents,
  positions,
  offers,
  resource,
  onReset,
}: {
  agents: Agent[];
  positions: Record<string, string>;
  offers: Offer[];
  /** The pool's own name — this footnote used to hardcode "credits". */
  resource: string;
  onReset: () => void;
}) {
  // Only parties who actually spoke: the human seat has no closing argument
  // unless they took a turn.
  const speakers = agents.filter((a) => positions[a.id]);

  return (
    <div className="absolute inset-0 z-20 flex flex-col items-center justify-center gap-6 overflow-y-auto bg-stage/80 p-6 backdrop-blur-[3px]">
      <h2 className="font-display animate-slide-in text-2xl font-bold tracking-[0.3em] text-glow text-primary">
        WHERE THEY LANDED
      </h2>
      <div className="grid w-full max-w-5xl gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {speakers.map((a, i) => {
          const moved = net(a.id, offers);
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
              <p className="mt-3 font-mono text-[13px] leading-snug text-foreground/85">
                “{positions[a.id]}”
              </p>
              <p className="mt-4 font-mono text-[11px] text-muted-foreground">
                net {moved >= 0 ? "+" : ""}
                {moved} {resource} moved
              </p>
            </article>
          );
        })}
      </div>
      <button
        onClick={onReset}
        className="rounded-md border border-primary px-6 py-2.5 font-display text-sm font-bold tracking-wide text-primary transition-colors hover:bg-primary hover:text-primary-foreground"
      >
        NEW DISCUSSION
      </button>
    </div>
  );
}
