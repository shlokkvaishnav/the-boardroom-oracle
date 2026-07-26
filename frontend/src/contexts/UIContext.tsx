import React, { createContext, useContext, useState } from 'react';

interface UIContextType {
  highlightedEdge: { source: string, target: string } | null;
  setHighlightedEdge: (edge: { source: string, target: string } | null) => void;
}

const UIContext = createContext<UIContextType | null>(null);

export function UIProvider({ children }: { children: React.ReactNode }) {
  const [highlightedEdge, setHighlightedEdge] = useState<{ source: string, target: string } | null>(null);
  return (
    <UIContext.Provider value={{ highlightedEdge, setHighlightedEdge }}>
      {children}
    </UIContext.Provider>
  );
}

export function useUI() {
  const ctx = useContext(UIContext);
  if (!ctx) throw new Error('useUI must be used within UIProvider');
  return ctx;
}
