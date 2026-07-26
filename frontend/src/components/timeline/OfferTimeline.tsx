import { useEffect, useRef } from 'react';
import { useEngine } from '../../contexts/EngineContext';
import { useUI } from '../../contexts/UIContext';
import { motion, AnimatePresence } from 'framer-motion';
import { CheckCircle2, XCircle, Clock } from 'lucide-react';

export function OfferTimeline() {
  const { state } = useEngine();
  const { highlightedEdge, setHighlightedEdge } = useUI();
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollLeft = scrollRef.current.scrollWidth;
    }
  }, [state.offerLog]);

  return (
    <div className="h-32 border-t border-border bg-card/80 backdrop-blur flex flex-col shrink-0 relative z-10">
      <div className="h-8 border-b border-border flex items-center px-4 shrink-0 bg-background/50">
        <h2 className="text-xs font-bold uppercase tracking-widest text-muted-foreground">Transaction Ledger</h2>
      </div>

      <div 
        ref={scrollRef}
        className="flex-1 overflow-x-auto p-4 flex items-center gap-4 scroll-smooth"
      >
        <AnimatePresence initial={false}>
          {state.offerLog.map((offer) => {
            const fromAgent = state.agents.find(a => a.id === offer.from);
            const toAgent = state.agents.find(a => a.id === offer.to);
            const isHighlighted = highlightedEdge?.source === offer.from && highlightedEdge?.target === offer.to;

            return (
              <motion.div
                key={offer.id}
                initial={{ opacity: 0, x: 20, scale: 0.95 }}
                animate={{ opacity: 1, x: 0, scale: 1 }}
                whileHover={{ scale: 1.05 }}
                onClick={() => {
                  if (isHighlighted) setHighlightedEdge(null);
                  else setHighlightedEdge({ source: offer.from, target: offer.to });
                }}
                className={`min-w-[240px] flex flex-col p-3 rounded border bg-background/90 cursor-pointer transition-colors ${
                  isHighlighted ? 'border-primary shadow-[0_0_10px_rgba(0,212,255,0.2)]' : 'border-border hover:border-white/20'
                }`}
                style={{ borderTopColor: fromAgent?.color, borderTopWidth: '2px' }}
              >
                <div className="flex items-center justify-between text-xs font-mono mb-2">
                  <div className="flex items-center gap-2">
                    <span style={{ color: fromAgent?.color }}>{fromAgent?.name}</span>
                    <span className="text-muted-foreground">→</span>
                    <span style={{ color: toAgent?.color }}>{toAgent?.name}</span>
                  </div>
                  <span className="text-muted-foreground opacity-50">R{offer.round}</span>
                </div>
                
                <div className="flex items-center justify-between">
                  <span className="font-bold text-white tracking-wide">{offer.amount} <span className="text-xs text-muted-foreground font-normal">units</span></span>
                  {offer.accepted === true && <CheckCircle2 className="w-4 h-4 text-green-500" />}
                  {offer.accepted === false && <XCircle className="w-4 h-4 text-destructive" />}
                  {offer.accepted === null && <Clock className="w-4 h-4 text-primary animate-pulse" />}
                </div>
              </motion.div>
            );
          })}
        </AnimatePresence>
        
        {state.offerLog.length === 0 && (
          <div className="text-muted-foreground opacity-50 text-xs italic font-mono px-2">
            Ledger empty...
          </div>
        )}
      </div>
    </div>
  );
}
