import type { Agent, AgentThought } from "@/lib/negotiation/types";

/**
 * Who changed their mind.
 *
 * The most interesting question about any argument, and until now the app
 * couldn't answer it — you could read six rounds of transcript and still not
 * know whether anyone had actually moved.
 *
 * One line per party, plotted −1 to +1 against the round. The line is the
 * story; the number beside it is the delta from where they opened, which is
 * the thing worth reading at a glance.
 */

const H = 68;
const PAD = 6;

interface Track {
  agent: Agent;
  points: Array<{ round: number; stance: number }>;
  delta: number;
}

function buildTracks(thoughts: AgentThought[], agents: Agent[]): Track[] {
  const tracks: Track[] = [];
  for (const agent of agents) {
    // One point per round: if a party spoke twice in a round, the later
    // reading wins, since stance is "where I am now".
    const byRound = new Map<number, number>();
    for (const t of thoughts) {
      if (t.agentId !== agent.id || t.stance == null || t.round <= 0) continue;
      byRound.set(t.round, t.stance);
    }
    if (byRound.size < 2) continue; // a single point is not a drift
    const points = [...byRound.entries()]
      .sort((a, b) => a[0] - b[0])
      .map(([round, stance]) => ({ round, stance }));
    tracks.push({
      agent,
      points,
      delta: points[points.length - 1].stance - points[0].stance,
    });
  }
  // Biggest mover first — that is the one worth looking at.
  return tracks.sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta));
}

export function StanceDrift({
  thoughts,
  agents,
  totalRounds,
}: {
  thoughts: AgentThought[];
  agents: Agent[];
  totalRounds: number;
}) {
  const tracks = buildTracks(thoughts, agents);
  if (tracks.length === 0) return null;

  const lastRound = Math.max(totalRounds, ...tracks.flatMap((t) => t.points.map((p) => p.round)));
  const x = (round: number) => ((round - 1) / Math.max(1, lastRound - 1)) * 100;
  const y = (stance: number) => PAD + ((1 - stance) / 2) * (H - PAD * 2);

  return (
    <section className="w-full max-w-3xl rounded-lg border border-border bg-card/70 p-3">
      <div className="flex items-baseline justify-between">
        <h4 className="font-display text-[10px] font-bold tracking-[0.22em] text-muted-foreground">
          WHO MOVED
        </h4>
        <span className="font-mono text-[10px] text-muted-foreground">against ← → for</span>
      </div>

      <div className="mt-2 space-y-1.5">
        {tracks.map(({ agent, points, delta }) => (
          <div key={agent.id} className="flex items-center gap-3">
            <span
              className="w-16 shrink-0 truncate font-mono text-[11px] font-bold"
              style={{ color: agent.color }}
            >
              {agent.name}
            </span>

            <svg
              viewBox={`0 0 100 ${H}`}
              preserveAspectRatio="none"
              className="h-8 min-w-0 flex-1"
              role="img"
              aria-label={`${agent.name} moved ${delta >= 0 ? "toward" : "away from"} the proposition by ${Math.abs(delta).toFixed(2)}`}
            >
              {/* Neutral line, so "crossed from against to for" is visible. */}
              <line
                x1="0"
                y1={y(0)}
                x2="100"
                y2={y(0)}
                stroke="var(--border)"
                strokeWidth="1"
                vectorEffect="non-scaling-stroke"
              />
              <polyline
                points={points.map((p) => `${x(p.round)},${y(p.stance)}`).join(" ")}
                fill="none"
                stroke={agent.color}
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
                vectorEffect="non-scaling-stroke"
              />
              <circle
                cx={x(points[points.length - 1].round)}
                cy={y(points[points.length - 1].stance)}
                r="2.5"
                fill={agent.color}
                vectorEffect="non-scaling-stroke"
              />
            </svg>

            <span
              className="w-14 shrink-0 text-right font-mono text-[11px] tabular-nums"
              style={{
                color: Math.abs(delta) < 0.05 ? "var(--muted-foreground)" : "var(--foreground)",
              }}
            >
              {Math.abs(delta) < 0.05 ? "held" : `${delta > 0 ? "+" : ""}${delta.toFixed(2)}`}
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}
