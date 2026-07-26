import { useEffect, useRef } from "react";
import { Search } from "lucide-react";
import type { Agent, AgentThought, SearchRecord } from "@/lib/negotiation/types";

/** Hostname alone — a full URL would wrap three times in this column. */
function host(url: string) {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

/**
 * What an agent actually looked up before speaking.
 *
 * The backend stamps these from a tool call that really ran, so unlike anything
 * else in the feed they cannot be hallucinated. Worth showing for exactly that
 * reason: it is the difference between an agent asserting a number and an agent
 * citing one.
 */
function Citations({ searched }: { searched: SearchRecord[] }) {
  if (searched.length === 0) return null;
  return (
    <span className="mt-1 flex flex-wrap items-center gap-1.5">
      <span
        className="inline-flex items-center gap-1 text-[10px] tracking-wider text-agent-4"
        title={`searched: ${searched[0].query}`}
      >
        <Search className="size-3" />
        {searched[0].query}
      </span>
      {searched.map((record, i) => (
        <a
          key={`${record.sourceUrl}-${i}`}
          href={record.sourceUrl}
          target="_blank"
          rel="noreferrer noopener"
          title={record.snippet}
          className="rounded border border-border px-1.5 py-0.5 text-[10px] text-muted-foreground transition-colors hover:border-agent-4 hover:text-agent-4"
        >
          {host(record.sourceUrl)}
        </a>
      ))}
    </span>
  );
}

/**
 * The transcript of the discussion.
 *
 * The wire field is still called `thought` for contract compatibility, but
 * it is no longer private reasoning � agents are prompted to speak to each
 * other, and this is what they said aloud. Hence "table talk" rather than
 * "thoughts", which described the old behaviour and would now be a lie.
 */

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
          TABLE TALK
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
            <span className="text-primary">&gt;</span> nobody has spoken yet…
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
              <span className="text-border">: </span>
              <span className="text-foreground/85">{t.text}</span>
              <Citations searched={t.searched} />
            </p>
          );
        })}
      </div>
    </section>
  );
}
