import { useEffect, useRef } from "react";
import type { Agent, AgentThought } from "@/lib/negotiation/types";

export function ThoughtsFeed({ thoughts, agents }: { thoughts: AgentThought[]; agents: Agent[] }) {
  const ref = useRef<HTMLDivElement>(null);
  const stick = useRef(true);

  useEffect(() => {
    const el = ref.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [thoughts]);

  const byId = new Map(agents.map((a) => [a.id, a]));
  const visible = thoughts.slice(-40);

  return (
    <section className="panel flex min-h-0 flex-col rounded-lg">
      <header className="flex items-center justify-between border-b border-border px-4 py-2.5">
        <h2 className="font-display text-xs font-bold tracking-[0.22em] text-muted-foreground">
          AGENT THOUGHTS
        </h2>
        <span className="size-2 animate-pulse rounded-full bg-trust-pos" />
      </header>
      <div
        ref={ref}
        onScroll={(e) => {
          const el = e.currentTarget;
          stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
        }}
        className="min-h-0 flex-1 space-y-3 overflow-y-auto px-4 py-3 font-mono text-[13px] leading-snug"
      >
        {visible.length === 0 && (
          <p className="text-muted-foreground">
            <span className="text-primary">&gt;</span> awaiting session…
          </p>
        )}
        {visible.map((t, i) => {
          const agent = byId.get(t.agentId);
          return (
            <p
              key={`${t.timestamp}-${i}`}
              className="animate-slide-in"
              style={{ color: "var(--muted-foreground)" }}
            >
              <span
                className="font-bold text-glow"
                style={{ color: agent?.color ?? "var(--primary)" }}
              >
                {agent?.name ?? t.agentId}
              </span>
              <span className="text-border"> :: </span>
              <span className="text-foreground/85">{t.text}</span>
            </p>
          );
        })}
      </div>
    </section>
  );
}
