import React, { createContext, useContext } from 'react';
import { useMockEngine } from '../hooks/use-mock-engine';

type EngineContextType = ReturnType<typeof useMockEngine>;

const EngineContext = createContext<EngineContextType | null>(null);

export function EngineProvider({ children }: { children: React.ReactNode }) {
  const engine = useMockEngine();
  return <EngineContext.Provider value={engine}>{children}</EngineContext.Provider>;
}

export function useEngine() {
  const ctx = useContext(EngineContext);
  if (!ctx) throw new Error('useEngine must be used within EngineProvider');
  return ctx;
}
