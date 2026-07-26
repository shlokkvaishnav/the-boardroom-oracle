import { useState } from 'react';
import { HeaderStrip } from '../components/HeaderStrip';
import { ForceGraph } from '../components/graph/ForceGraph';
import { ThoughtFeed } from '../components/thoughts/ThoughtFeed';
import { OfferTimeline } from '../components/timeline/OfferTimeline';
import { VoiceModal } from '../components/voice/VoiceModal';
import { EngineProvider } from '../contexts/EngineContext';
import { UIProvider } from '../contexts/UIContext';

export function Boardroom() {
  const [isMicOpen, setIsMicOpen] = useState(false);

  return (
    <EngineProvider>
      <UIProvider>
        <div className="flex flex-col w-full h-screen max-h-[100dvh] overflow-hidden bg-background text-foreground">
          {/* Top Header */}
          <HeaderStrip onOpenMic={() => setIsMicOpen(true)} />

          {/* Main Content Area */}
          <div className="flex-1 flex overflow-hidden">
            {/* Center Stage: Graph */}
            <div className="flex-1 relative flex flex-col">
              <ForceGraph />
              
              {/* Bottom Strip: Timeline */}
              <OfferTimeline />
            </div>

            {/* Right Panel: Thoughts */}
            <ThoughtFeed />
          </div>

          {/* Overlays */}
          <VoiceModal isOpen={isMicOpen} onClose={() => setIsMicOpen(false)} />
        </div>
      </UIProvider>
    </EngineProvider>
  );
}
