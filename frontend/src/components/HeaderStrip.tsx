import { Play, RotateCcw, Mic } from 'lucide-react';
import { useEngine } from '../contexts/EngineContext';

export function HeaderStrip({ onOpenMic }: { onOpenMic: () => void }) {
  const { state, start, reset } = useEngine();

  return (
    <header className="h-16 border-b border-border bg-background/80 backdrop-blur flex items-center justify-between px-6 shrink-0 relative z-10">
      <div className="flex items-center gap-6">
        <h1 className="text-xl font-bold tracking-widest text-white uppercase flex items-center gap-2">
          <div className="w-2 h-2 rounded-full bg-primary animate-pulse shadow-[var(--shadow-neon-blue)]" />
          Boardroom Oracle
        </h1>
        
        <div className="h-6 w-px bg-border" />
        
        <div className="font-mono text-sm text-muted-foreground uppercase tracking-wider">
          {state.status === 'idle' ? 'System Ready' : state.status === 'revealed' ? 'Simulation Complete' : `Round ${state.round} of 8`}
        </div>
      </div>

      <div className="flex items-center gap-8">
        <div className="flex items-center gap-3 px-4 py-1.5 rounded-full bg-card border border-card-border">
          <span className="text-primary">⬡</span>
          <span className="font-mono text-sm font-bold text-white">{state.pool.resource}</span>
          <span className="font-mono text-xs text-muted-foreground">—</span>
          <span className="font-mono text-sm text-primary">{state.pool.total} units</span>
        </div>

        <div className="flex items-center gap-3">
          {state.status === 'idle' && (
            <button
              onClick={start}
              className="flex items-center gap-2 px-4 py-2 bg-primary/10 text-primary border border-primary/30 rounded hover:bg-primary/20 hover:border-primary transition-all shadow-[var(--shadow-neon-blue)] text-sm font-bold uppercase tracking-wider cursor-pointer"
            >
              <Play className="w-4 h-4" />
              Start Negotiation
            </button>
          )}

          {state.status !== 'idle' && (
            <button
              onClick={reset}
              className="flex items-center gap-2 px-4 py-2 bg-destructive/10 text-destructive border border-destructive/30 rounded hover:bg-destructive/20 hover:border-destructive transition-all text-sm font-bold uppercase tracking-wider cursor-pointer"
            >
              <RotateCcw className="w-4 h-4" />
              Reset
            </button>
          )}

          <button
            onClick={onOpenMic}
            className={`w-10 h-10 rounded-full flex items-center justify-center border transition-all cursor-pointer ${
              state.status === 'idle' 
                ? 'opacity-50 cursor-not-allowed border-muted bg-muted text-muted-foreground'
                : 'border-destructive text-destructive bg-destructive/10 hover:bg-destructive/20 hover:shadow-[0_0_15px_rgba(239,68,68,0.5)]'
            }`}
            disabled={state.status === 'idle'}
            title="Inject Human Voice Offer"
          >
            <Mic className="w-5 h-5" />
          </button>
        </div>
      </div>
    </header>
  );
}
