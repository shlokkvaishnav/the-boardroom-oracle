import React, { useEffect, useRef, useState, useMemo } from 'react';
import * as d3 from 'd3-force';
import { motion, AnimatePresence } from 'framer-motion';
import { useEngine } from '../../contexts/EngineContext';
import { useUI } from '../../contexts/UIContext';

interface NodeData extends d3.SimulationNodeDatum {
  id: string;
  label: string;
  color: string;
  isHuman: boolean;
  score?: number;
  objective?: string;
}

interface LinkData extends d3.SimulationLinkDatum<NodeData> {
  weight: number;
  lastOfferAccepted: boolean;
}

export function ForceGraph() {
  const containerRef = useRef<HTMLDivElement>(null);
  const { state } = useEngine();
  const { highlightedEdge } = useUI();

  const [nodes, setNodes] = useState<NodeData[]>([]);
  const [links, setLinks] = useState<LinkData[]>([]);
  const [dimensions, setDimensions] = useState({ width: 800, height: 600 });

  // Handle resize
  useEffect(() => {
    if (!containerRef.current) return;
    const observer = new ResizeObserver(entries => {
      for (let entry of entries) {
        setDimensions({
          width: entry.contentRect.width,
          height: entry.contentRect.height
        });
      }
    });
    observer.observe(containerRef.current);
    return () => observer.disconnect();
  }, []);

  // Initialize and run force simulation
  useEffect(() => {
    if (dimensions.width === 0 || dimensions.height === 0) return;

    const mappedNodes: NodeData[] = state.trustGraph.nodes.map(n => {
      const agent = state.agents.find(a => a.id === n.id);
      const revealed = state.revealedObjectives?.[n.id];
      return {
        id: n.id,
        label: n.label,
        color: agent?.color || '#fff',
        isHuman: agent?.isHuman || false,
        score: revealed?.score,
        objective: revealed?.objective
      };
    });

    const mappedLinks: LinkData[] = state.trustGraph.edges.map(e => ({
      source: e.source,
      target: e.target,
      weight: e.weight,
      lastOfferAccepted: e.lastOfferAccepted
    }));

    const simulation = d3.forceSimulation<NodeData, LinkData>(mappedNodes)
      .force('link', d3.forceLink<NodeData, LinkData>(mappedLinks)
        .id(d => d.id)
        .distance(180)
        .strength(d => Math.abs(d.weight) * 0.5 + 0.1))
      .force('charge', d3.forceManyBody().strength(-800))
      .force('center', d3.forceCenter(dimensions.width / 2, dimensions.height / 2))
      .force('collide', d3.forceCollide().radius(80));

    simulation.on('tick', () => {
      setNodes([...simulation.nodes()]);
      // Links are mutated by d3 to have source/target as node objects instead of strings
      setLinks([...mappedLinks]); 
    });

    // Re-heat simulation slightly when state changes to let it settle into new weights
    simulation.alpha(0.3).restart();

    return () => {
      simulation.stop();
    };
  }, [state.trustGraph, state.agents, dimensions, state.revealedObjectives]);

  return (
    <div ref={containerRef} className="flex-1 relative overflow-hidden bg-background">
      {/* Glitch/Grid Background */}
      <div className="absolute inset-0 glitch-overlay opacity-50" />
      
      {/* Particles effect simple wrapper */}
      <div className="absolute inset-0 pointer-events-none" style={{ background: 'radial-gradient(circle at center, transparent 0%, #0a0a0f 100%)' }} />

      <svg width="100%" height="100%" className="relative z-10">
        <defs>
          <filter id="glow-green">
            <feGaussianBlur stdDeviation="4" result="coloredBlur"/>
            <feMerge>
              <feMergeNode in="coloredBlur"/>
              <feMergeNode in="SourceGraphic"/>
            </feMerge>
          </filter>
          <filter id="glow-red">
            <feGaussianBlur stdDeviation="4" result="coloredBlur"/>
            <feMerge>
              <feMergeNode in="coloredBlur"/>
              <feMergeNode in="SourceGraphic"/>
            </feMerge>
          </filter>
          <filter id="glow-node">
            <feGaussianBlur stdDeviation="6" result="coloredBlur"/>
            <feMerge>
              <feMergeNode in="coloredBlur"/>
              <feMergeNode in="SourceGraphic"/>
            </feMerge>
          </filter>
        </defs>

        <AnimatePresence>
          {links.map((link, i) => {
            const source = link.source as unknown as NodeData;
            const target = link.target as unknown as NodeData;
            if (!source.x || !source.y || !target.x || !target.y) return null;

            const isHighlighted = (highlightedEdge?.source === source.id && highlightedEdge?.target === target.id) ||
                                  (highlightedEdge?.source === target.id && highlightedEdge?.target === source.id);
            const edgeColor = link.weight >= 0 ? '#22c55e' : '#ef4444'; // Green for trust, Red for distrust
            const opacity = Math.max(0.2, Math.abs(link.weight));
            const strokeWidth = Math.max(2, Math.abs(link.weight) * 10);

            return (
              <motion.line
                key={`link-${source.id}-${target.id}`}
                x1={source.x}
                y1={source.y}
                x2={target.x}
                y2={target.y}
                stroke={edgeColor}
                initial={{ opacity: 0 }}
                animate={{ 
                  opacity: isHighlighted ? 1 : opacity,
                  strokeWidth: isHighlighted ? strokeWidth * 2 : strokeWidth
                }}
                transition={{ duration: 0.5 }}
                filter={link.weight >= 0 ? 'url(#glow-green)' : 'url(#glow-red)'}
                strokeLinecap="round"
              />
            );
          })}
        </AnimatePresence>

        <AnimatePresence>
          {nodes.map((node) => {
            if (!node.x || !node.y) return null;

            const isRevealed = state.status === 'revealed';

            return (
              <motion.g
                key={node.id}
                initial={{ opacity: 0, scale: 0 }}
                animate={{ 
                  opacity: 1, 
                  scale: 1,
                  x: node.x,
                  y: node.y
                }}
                transition={{ type: "spring", stiffness: 50, damping: 10 }}
              >
                <circle
                  r={isRevealed ? 45 : 35}
                  fill={state.status === 'revealed' ? '#12121a' : '#12121a'}
                  stroke={node.color}
                  strokeWidth={3}
                  filter="url(#glow-node)"
                />
                <circle
                  r={isRevealed ? 45 : 35}
                  fill={state.status === 'revealed' ? '#12121a' : node.color}
                  opacity={isRevealed ? 1 : 0.1}
                />

                {/* Agent Name */}
                <text
                  textAnchor="middle"
                  y={isRevealed ? -15 : 5}
                  fill={isRevealed ? node.color : "#ffffff"}
                  className="font-sans font-bold text-sm tracking-widest pointer-events-none"
                  style={{ textShadow: `0 0 10px ${node.color}` }}
                >
                  {node.label}
                </text>

                {/* Reveal Stats */}
                {isRevealed && (
                  <>
                    <text
                      textAnchor="middle"
                      y={8}
                      fill="#ffffff"
                      className="font-mono text-[10px] pointer-events-none opacity-80"
                    >
                      SCORE
                    </text>
                    <text
                      textAnchor="middle"
                      y={25}
                      fill={node.color}
                      className="font-mono text-lg font-bold pointer-events-none"
                      style={{ textShadow: `0 0 10px ${node.color}` }}
                    >
                      {node.score}%
                    </text>
                    <foreignObject x="-100" y="55" width="200" height="80">
                      <div className="text-center font-mono text-xs text-white/80 leading-tight bg-background/80 border border-white/10 p-2 rounded backdrop-blur">
                        <span className="text-muted-foreground uppercase text-[9px] block mb-1">Hidden Objective</span>
                        {node.objective}
                      </div>
                    </foreignObject>
                  </>
                )}
              </motion.g>
            );
          })}
        </AnimatePresence>
      </svg>
    </div>
  );
}
