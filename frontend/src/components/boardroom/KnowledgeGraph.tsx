import { useEffect, useMemo, useRef, useState } from "react";
import { ExternalLink } from "lucide-react";
import type { Agent, KnowledgeEdge, KnowledgeNode } from "@/lib/negotiation/types";

/**
 * The argument, drawn.
 *
 * A deliberately different rendering from the trust graph next door. That one
 * has four nodes and one edge type, so it can afford glow and physics. This one
 * grows a node per claim and per entity, and claim labels are whole sentences —
 * so the layout is *seeded by kind* into columns rather than left to a force
 * simulation, which at this node count would spend its time untangling itself
 * and still put the text on top of itself.
 *
 * Claims are dots in their author's colour, not text. The sentence appears on
 * hover and on click, in one place, where there is room to read it.
 */

interface Placed {
  node: KnowledgeNode;
  x: number;
  y: number;
  color: string;
}

const KIND_ORDER: Record<KnowledgeNode["kind"], number> = {
  party: 0,
  claim: 1,
  entity: 2,
  evidence: 3,
};

/** Claims inherit their author's colour; everything else has its own. */
const ENTITY_COLOR = "var(--agent-3)";
const EVIDENCE_COLOR = "var(--muted-foreground)";

export function KnowledgeGraph({
  nodes,
  edges,
  agents,
}: {
  nodes: KnowledgeNode[];
  edges: KnowledgeEdge[];
  agents: Agent[];
}) {
  const [selected, setSelected] = useState<string | null>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 800, h: 460 });

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => {
      const rect = el.getBoundingClientRect();
      setSize({ w: Math.max(320, rect.width), h: Math.max(240, rect.height) });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const colorOf = useMemo(() => new Map(agents.map((a) => [a.id, a.color])), [agents]);

  const placed = useMemo(() => {
    const byKind: Record<string, KnowledgeNode[]> = {
      party: [],
      claim: [],
      entity: [],
      evidence: [],
    };
    for (const node of [...nodes].sort((a, b) => KIND_ORDER[a.kind] - KIND_ORDER[b.kind])) {
      byKind[node.kind]?.push(node);
    }

    // One column per kind, so an edge always runs left to right and the shape
    // of the argument — who claimed what, about what — is readable at a glance.
    const columns: Array<KnowledgeNode["kind"]> = ["party", "claim", "entity", "evidence"];
    const active = columns.filter((k) => byKind[k].length > 0);
    const map = new Map<string, Placed>();

    active.forEach((kind, columnIndex) => {
      const column = byKind[kind];
      const x = ((columnIndex + 0.5) / active.length) * size.w;
      column.forEach((node, i) => {
        const y = ((i + 0.5) / column.length) * (size.h - 48) + 24;
        map.set(node.id, {
          node,
          x,
          y,
          color:
            node.kind === "claim"
              ? (colorOf.get(node.authorId ?? "") ?? "var(--primary)")
              : node.kind === "party"
                ? (colorOf.get(node.id) ?? "var(--primary)")
                : node.kind === "entity"
                  ? ENTITY_COLOR
                  : EVIDENCE_COLOR,
        });
      });
    });
    return map;
  }, [nodes, size, colorOf]);

  const detail = selected ? placed.get(selected)?.node : undefined;
  const claimCount = nodes.filter((n) => n.kind === "claim").length;

  return (
    <div ref={wrapRef} className="relative h-full w-full">
      {claimCount === 0 && (
        <div className="pointer-events-none absolute inset-0 grid place-items-center px-8 text-center">
          <p className="font-mono text-sm text-muted-foreground">
            nothing claimed yet — agents only make claims when the table has a{" "}
            <span className="text-agent-4">topic</span>
          </p>
        </div>
      )}

      <svg
        width={size.w}
        height={size.h}
        className="absolute inset-0"
        role="img"
        aria-label={`Knowledge graph: ${claimCount} claims`}
      >
        {edges.map((edge, i) => {
          const from = placed.get(edge.source);
          const to = placed.get(edge.target);
          if (!from || !to) return null;
          const dim = selected && selected !== edge.source && selected !== edge.target;
          // A curve, so two edges between the same columns stay distinguishable.
          const mx = (from.x + to.x) / 2;
          return (
            <path
              key={`${edge.source}-${edge.target}-${edge.kind}-${i}`}
              d={`M ${from.x} ${from.y} C ${mx} ${from.y}, ${mx} ${to.y}, ${to.x} ${to.y}`}
              fill="none"
              stroke={
                edge.kind === "contradicts"
                  ? "var(--trust-neg)"
                  : edge.kind === "supports"
                    ? "var(--trust-pos)"
                    : from.color
              }
              strokeWidth={edge.kind === "asserts" ? 1.6 : 1}
              strokeDasharray={edge.kind === "cites" || edge.kind === "about" ? "3 4" : undefined}
              opacity={dim ? 0.08 : 0.42}
            />
          );
        })}

        {[...placed.values()].map(({ node, x, y, color }) => {
          const isClaim = node.kind === "claim";
          const isSelected = selected === node.id;
          const r = node.kind === "party" ? 9 : isClaim ? 6 : 4;
          return (
            <g
              key={node.id}
              onClick={() => setSelected(isSelected ? null : node.id)}
              onMouseEnter={() => setSelected(node.id)}
              className="cursor-pointer"
            >
              <title>{node.label}</title>
              {/* A generous invisible hit area — the dots are small on purpose. */}
              <circle cx={x} cy={y} r={14} fill="transparent" />
              <circle
                cx={x}
                cy={y}
                r={r + (isSelected ? 3 : 0)}
                fill={node.kind === "evidence" ? "none" : color}
                stroke={color}
                strokeWidth={1}
                opacity={selected && !isSelected ? 0.35 : 1}
              />
              {node.kind !== "claim" && (
                <text
                  x={x + r + 6}
                  y={y + 4}
                  className="pointer-events-none fill-muted-foreground font-mono"
                  style={{ fontSize: 10 }}
                >
                  {node.label.length > 22 ? `${node.label.slice(0, 21)}…` : node.label}
                </text>
              )}
            </g>
          );
        })}
      </svg>

      {detail && (
        <div className="pointer-events-auto absolute inset-x-3 bottom-3 rounded-lg border border-border bg-card/95 p-3 backdrop-blur-sm">
          <div className="flex items-center gap-2 font-mono text-[10px] uppercase tracking-widest text-muted-foreground">
            <span>{detail.kind}</span>
            {detail.claimKind && <span className="text-agent-4">{detail.claimKind}</span>}
            {detail.round != null && <span>round {detail.round}</span>}
          </div>
          <p className="mt-1 font-mono text-[13px] leading-snug text-foreground/90">
            {detail.label}
          </p>
          {detail.sourceUrl && (
            <a
              href={detail.sourceUrl}
              target="_blank"
              rel="noreferrer noopener"
              className="mt-1 inline-flex items-center gap-1 font-mono text-[11px] text-agent-4 hover:underline"
            >
              <ExternalLink className="size-3" />
              {detail.sourceUrl}
            </a>
          )}
        </div>
      )}
    </div>
  );
}
