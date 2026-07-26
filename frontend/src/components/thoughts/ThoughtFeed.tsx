import { useEffect, useRef } from 'react';
import { useEngine } from '../../contexts/EngineContext';
import { motion, AnimatePresence } from 'framer-motion';

export function ThoughtFeed() {
  const { state } = useEngine();
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [state.agentThoughts]);

  return (
    <div className="w-[30%] h-full border-l border-border bg-card/50 backdrop-blur flex flex-col shrink-0 relative z-10">
      <div className="h-10 border-b border-border flex items-center px-4 shrink-0 bg-background/50">
        <h2 className="text-xs font-bold uppercase tracking-widest text-muted-foreground">Live Intelligence Intercept</h2>
      </div>

      <div 
        ref={scrollRef}
        className="flex-1 overflow-y-auto p-4 flex flex-col gap-3 font-mono text-sm"
      >
        <AnimatePresence initial={false}>
          {state.agentThoughts.map((thought) => {
            const agent = state.agents.find(a => a.id === thought.agentId);
            const color = agent?.color || '#ffffff';

            return (
              <motion.div
                key={thought.id}
                initial={{ opacity: 0, y: 10, x: -10 }}
                animate={{ opacity: 1, y: 0, x: 0 }}
                className="flex flex-col gap-1 p-3 rounded bg-background/80 border border-white/5"
                style={{ borderLeftColor: color, borderLeftWidth: '2px' }}
              >
                <div className="flex items-center justify-between text-xs opacity-70">
                  <span style={{ color }}>{agent?.name}</span>
                  <span className="opacity-50">{new Date(thought.timestamp).toLocaleTimeString([], { hour12: false, hour: '2-digit', minute: '2-digit', second:'2-digit' })}</span>
                </div>
                <div className="text-white/90 whitespace-pre-wrap leading-relaxed">
                  {`> ${thought.text}`}
                </div>
              </motion.div>
            );
          })}
          
          {state.agentThoughts.length === 0 && state.status === 'idle' && (
            <div className="text-muted-foreground opacity-50 text-xs italic mt-4">
              Awaiting intercept...
            </div>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}
