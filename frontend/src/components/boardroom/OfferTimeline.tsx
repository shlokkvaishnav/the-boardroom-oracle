import { useEffect, useRef } from "react";
import { Check, X, Loader } from "lucide-react";
import type { Agent, Offer } from "@/lib/negotiation/types";

export function OfferTimeline({
  offers,
  agents,
  selected,
  onSelect,
}: {
  offers: Offer[];
  agents: Agent[];
  selected: number | null;
  onSelect: (index: number | null) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (el) el.scrollLeft = el.scrollWidth;
  }, [offers.length]);

  const byId = new Map(agents.map((a) => [a.id, a]));

  return (
    <section className="panel rounded-lg">
      <div className="flex items-center gap-4 px-4 py-2">
        <h2 className="font-display shrink-0 text-xs font-bold tracking-[0.22em] text-muted-foreground">
          OFFER LEDGER
        </h2>
        <div ref={ref} className="flex min-w-0 flex-1 gap-2 overflow-x-auto pb-1">
          {offers.length === 0 && (
            <span className="py-3 font-mono text-xs text-muted-foreground">
              no offers yet — start the negotiation
            </span>
          )}
          {offers.map((o, i) => {
            const from = byId.get(o.from);
            const to = byId.get(o.to);
            const active = selected === i;
            return (
              <button
                key={`${o.timestamp}-${i}`}
                onClick={() => onSelect(active ? null : i)}
                className={`animate-card-in shrink-0 rounded-md border px-3 py-2 text-left transition-colors ${
                  active
                    ? "border-primary bg-primary/10"
                    : "border-border bg-secondary/40 hover:border-primary/60"
                }`}
              >
                <div className="flex items-center gap-1.5 font-mono text-[11px] font-bold">
                  <span style={{ color: from?.color }}>{from?.name ?? o.from}</span>
                  <span className="text-muted-foreground">→</span>
                  <span style={{ color: to?.color }}>{to?.name ?? o.to}</span>
                </div>
                <div className="mt-1 flex items-center gap-2 font-mono text-sm">
                  <span className="font-bold text-foreground tabular-nums">{o.amount}</span>
                  <span className="text-[10px] text-muted-foreground">R{o.round}</span>
                  {o.accepted === null ? (
                    <Loader className="size-3.5 animate-spin text-muted-foreground" />
                  ) : o.accepted ? (
                    <Check className="size-3.5 text-trust-pos" />
                  ) : (
                    <X className="size-3.5 text-trust-neg" />
                  )}
                </div>
              </button>
            );
          })}
        </div>
      </div>
    </section>
  );
}
