import { useEffect, useRef } from "react";
import type { NegotiationState } from "@/lib/negotiation/types";

interface SimNode {
  id: string;
  label: string;
  color: string;
  isHuman: boolean;
  x: number;
  y: number;
  vx: number;
  vy: number;
  appear: number;
  pulse: number;
}

interface SimEdge {
  key: string;
  source: string;
  target: string;
  weight: number;
  display: number;
  appear: number;
  accepted: boolean;
  flow: number;
}

interface Particle {
  x: number;
  y: number;
  vx: number;
  vy: number;
  r: number;
}

const edgeKey = (s: string, t: string) => `${s}->${t}`;

/** Smooth red -> slate -> green ramp in oklch space. */
function trustColor(w: number) {
  const t = Math.max(-1, Math.min(1, w));
  const neg = [0.64, 0.24, 25];
  const mid = [0.5, 0.02, 265];
  const pos = [0.8, 0.2, 155];
  const [a, b, k] = t < 0 ? [neg, mid, 1 + t] : [mid, pos, t];
  const l = a[0] + (b[0] - a[0]) * k;
  const c = a[1] + (b[1] - a[1]) * k;
  const h = a[2] + (b[2] - a[2]) * k;
  return `oklch(${l.toFixed(3)} ${c.toFixed(3)} ${h.toFixed(1)})`;
}

export function TrustGraph({
  state,
  highlight,
  dimmed,
}: {
  state: NegotiationState;
  highlight: { source: string; target: string } | null;
  dimmed: boolean;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const nodesRef = useRef<Map<string, SimNode>>(new Map());
  const edgesRef = useRef<Map<string, SimEdge>>(new Map());
  const particlesRef = useRef<Particle[]>([]);
  const lastOfferRef = useRef(0);
  const stateRef = useRef(state);
  const highlightRef = useRef(highlight);
  const dimRef = useRef(dimmed);

  stateRef.current = state;
  highlightRef.current = highlight;
  dimRef.current = dimmed;

  // Sync graph model from incoming state (never resets positions).
  useEffect(() => {
    const nodes = nodesRef.current;
    const colorById = new Map(state.agents.map((a) => [a.id, a]));
    state.trustGraph.nodes.forEach((n, i) => {
      if (!nodes.has(n.id)) {
        const angle = (i / Math.max(1, state.trustGraph.nodes.length)) * Math.PI * 2;
        nodes.set(n.id, {
          id: n.id,
          label: n.label,
          color: colorById.get(n.id)?.color ?? "var(--agent-1)",
          isHuman: colorById.get(n.id)?.isHuman ?? false,
          x: Math.cos(angle) * 140,
          y: Math.sin(angle) * 140,
          vx: 0,
          vy: 0,
          appear: 0,
          pulse: 0,
        });
      } else {
        const node = nodes.get(n.id)!;
        node.label = n.label;
        node.color = colorById.get(n.id)?.color ?? node.color;
      }
    });

    const edges = edgesRef.current;
    state.trustGraph.edges.forEach((e) => {
      const key = edgeKey(e.source, e.target);
      const existing = edges.get(key);
      if (existing) {
        existing.weight = e.weight;
        existing.accepted = e.lastOfferAccepted;
      } else {
        edges.set(key, {
          key,
          source: e.source,
          target: e.target,
          weight: e.weight,
          display: 0,
          appear: 0,
          accepted: e.lastOfferAccepted,
          flow: 0,
        });
      }
    });
    const live = new Set(state.trustGraph.edges.map((e) => edgeKey(e.source, e.target)));
    edges.forEach((e, k) => {
      if (!live.has(k)) edges.delete(k);
    });
  }, [state]);

  // Pulse nodes + edge flow when a new offer lands.
  useEffect(() => {
    const log = state.offerLog;
    if (log.length <= lastOfferRef.current) {
      lastOfferRef.current = log.length;
      return;
    }
    for (let i = lastOfferRef.current; i < log.length; i++) {
      const o = log[i];
      const from = nodesRef.current.get(o.from);
      const to = nodesRef.current.get(o.to);
      if (from) from.pulse = 1;
      if (to) to.pulse = 0.75;
      const e = edgesRef.current.get(edgeKey(o.from, o.to));
      if (e) e.flow = 1;
    }
    lastOfferRef.current = log.length;
  }, [state.offerLog]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    let raf = 0;
    let w = 0;
    let h = 0;
    const css = getComputedStyle(document.documentElement);
    const resolve = (c: string) =>
      c.startsWith("var(") ? css.getPropertyValue(c.slice(4, -1)).trim() || "#7dd3fc" : c;

    const resize = () => {
      const rect = canvas.getBoundingClientRect();
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      w = rect.width;
      h = rect.height;
      canvas.width = Math.max(1, Math.floor(w * dpr));
      canvas.height = Math.max(1, Math.floor(h * dpr));
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      if (particlesRef.current.length === 0) {
        particlesRef.current = Array.from({ length: 54 }, () => ({
          x: Math.random() * w,
          y: Math.random() * h,
          vx: (Math.random() - 0.5) * 0.16,
          vy: (Math.random() - 0.5) * 0.16,
          r: Math.random() * 1.6 + 0.4,
        }));
      }
    };
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(canvas);

    let t = 0;
    const draw = () => {
      t += 1 / 60;
      const cx = w / 2;
      const cy = h / 2;
      const nodes = [...nodesRef.current.values()];
      const edges = [...edgesRef.current.values()];
      const revealing = !!stateRef.current.closingPositions;
      const radius = Math.min(w, h) * 0.32;

      // ---- physics
      nodes.forEach((n, i) => {
        n.appear = Math.min(1, n.appear + 0.05);
        n.pulse *= 0.94;
        if (revealing) {
          const angle = (i / Math.max(1, nodes.length)) * Math.PI * 2 - Math.PI / 2;
          n.vx += (Math.cos(angle) * radius - n.x) * 0.012;
          n.vy += (Math.sin(angle) * radius - n.y) * 0.012;
        } else {
          n.vx += -n.x * 0.0022;
          n.vy += -n.y * 0.0022;
          nodes.forEach((m) => {
            if (m === n) return;
            const dx = n.x - m.x;
            const dy = n.y - m.y;
            const d2 = Math.max(400, dx * dx + dy * dy);
            const f = 42000 / d2;
            n.vx += (dx / Math.sqrt(d2)) * f * 0.02;
            n.vy += (dy / Math.sqrt(d2)) * f * 0.02;
          });
        }
        n.vx *= 0.86;
        n.vy *= 0.86;
      });

      edges.forEach((e) => {
        e.appear = Math.min(1, e.appear + 0.045);
        e.display += (e.weight - e.display) * 0.06;
        e.flow = Math.max(0, e.flow - 0.008);
        if (revealing) return;
        const a = nodesRef.current.get(e.source);
        const b = nodesRef.current.get(e.target);
        if (!a || !b) return;
        const dx = b.x - a.x;
        const dy = b.y - a.y;
        const dist = Math.hypot(dx, dy) || 1;
        const rest = 250 - e.display * 70;
        const f = (dist - rest) * 0.0016;
        a.vx += (dx / dist) * f;
        a.vy += (dy / dist) * f;
        b.vx -= (dx / dist) * f;
        b.vy -= (dy / dist) * f;
      });

      const bound = Math.min(w, h) * 0.42;
      nodes.forEach((n) => {
        n.x = Math.max(-bound, Math.min(bound, n.x + n.vx));
        n.y = Math.max(-bound, Math.min(bound, n.y + n.vy));
      });

      // ---- background
      ctx.clearRect(0, 0, w, h);
      ctx.save();
      ctx.globalAlpha = 0.32;
      ctx.strokeStyle = resolve("var(--border)");
      ctx.lineWidth = 1;
      const grid = 48;
      const off = (t * 6) % grid;
      ctx.beginPath();
      for (let x = -off; x < w; x += grid) {
        ctx.moveTo(x, 0);
        ctx.lineTo(x, h);
      }
      for (let y = -off; y < h; y += grid) {
        ctx.moveTo(0, y);
        ctx.lineTo(w, y);
      }
      ctx.stroke();
      ctx.restore();

      ctx.save();
      ctx.fillStyle = resolve("var(--agent-3)");
      particlesRef.current.forEach((p) => {
        p.x = (p.x + p.vx + w) % w;
        p.y = (p.y + p.vy + h) % h;
        ctx.globalAlpha = 0.25;
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
        ctx.fill();
      });
      ctx.restore();

      const hl = highlightRef.current;
      const globalDim = dimRef.current ? 0.25 : 1;

      // ---- edges
      ctx.save();
      ctx.translate(cx, cy);
      edges.forEach((e) => {
        const a = nodesRef.current.get(e.source);
        const b = nodesRef.current.get(e.target);
        if (!a || !b) return;
        const isHl = hl && hl.source === e.source && hl.target === e.target;
        const alpha = (hl && !isHl ? 0.14 : 0.9) * e.appear * globalDim;
        const mx = (a.x + b.x) / 2;
        const my = (a.y + b.y) / 2;
        const nx = -(b.y - a.y);
        const ny = b.x - a.x;
        const len = Math.hypot(nx, ny) || 1;
        const bend = 26;
        const qx = mx + (nx / len) * bend;
        const qy = my + (ny / len) * bend;

        ctx.globalAlpha = alpha;
        ctx.strokeStyle = trustColor(e.display);
        ctx.lineWidth = 1.2 + Math.abs(e.display) * 7 + (isHl ? 3 : 0);
        ctx.lineCap = "round";
        ctx.shadowBlur = isHl ? 26 : 14;
        ctx.shadowColor = trustColor(e.display);
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.quadraticCurveTo(qx, qy, b.x, b.y);
        ctx.stroke();

        if (e.flow > 0) {
          const k = 1 - e.flow;
          const px = (1 - k) * (1 - k) * a.x + 2 * (1 - k) * k * qx + k * k * b.x;
          const py = (1 - k) * (1 - k) * a.y + 2 * (1 - k) * k * qy + k * k * b.y;
          ctx.globalAlpha = Math.min(1, e.flow * 1.4) * globalDim;
          ctx.fillStyle = e.accepted ? resolve("var(--trust-pos)") : resolve("var(--trust-neg)");
          ctx.beginPath();
          ctx.arc(px, py, 5, 0, Math.PI * 2);
          ctx.fill();
        }
        ctx.shadowBlur = 0;
      });

      // ---- nodes
      nodes.forEach((n) => {
        const color = resolve(n.color);
        const r = 30 + n.pulse * 8;
        ctx.globalAlpha = n.appear * globalDim;

        if (n.pulse > 0.02) {
          ctx.globalAlpha = n.pulse * 0.35 * globalDim;
          ctx.fillStyle = color;
          ctx.beginPath();
          ctx.arc(n.x, n.y, r + (1 - n.pulse) * 46, 0, Math.PI * 2);
          ctx.fill();
        }

        ctx.globalAlpha = n.appear * globalDim;
        ctx.shadowBlur = 30 + n.pulse * 30;
        ctx.shadowColor = color;
        ctx.fillStyle = resolve("var(--stage)");
        ctx.beginPath();
        ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
        ctx.fill();
        ctx.lineWidth = 3;
        ctx.strokeStyle = color;
        if (n.isHuman) ctx.setLineDash([7, 5]);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.shadowBlur = 0;

        ctx.fillStyle = color;
        ctx.font = "700 15px 'Space Grotesk', sans-serif";
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText(n.label, n.x, n.y + r + 22);
      });
      ctx.restore();

      raf = requestAnimationFrame(draw);
    };
    raf = requestAnimationFrame(draw);

    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
    };
  }, []);

  return <canvas ref={canvasRef} className="h-full w-full" aria-hidden />;
}
