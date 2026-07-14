"use client";

import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

// Roadmap Section 5: "Floating chat launcher (bottom-right), available
// from every tab, slides open a panel -- not a top-level tab." GalaxyView's
// detail panel and ChatLauncher (the floating button + slide-in panel) are
// SIBLINGS under the root layout, not parent/child, so "Ask about this
// resource" needs a way to open the floating panel (pre-scoped to a
// resource) from anywhere in the tree without a route navigation. Plain
// React Context is enough for this app's scale -- no new state library.
export interface ChatScope {
  id: string;
  label: string;
}

interface ChatLauncherState {
  isOpen: boolean;
  scope: ChatScope | null;
  openChat: (scope?: ChatScope) => void;
  closeChat: () => void;
}

const ChatLauncherContext = createContext<ChatLauncherState | null>(null);

export function ChatLauncherProvider({ children }: { children: ReactNode }) {
  const [isOpen, setIsOpen] = useState(false);
  const [scope, setScope] = useState<ChatScope | null>(null);

  // Called with no argument for the plain launcher button (opens a fresh,
  // unscoped chat -- clearing any previous "ask about" scope) and with a
  // resource for the galaxy detail panel's "Ask about this resource" button.
  const openChat = useCallback((next?: ChatScope) => {
    setScope(next ?? null);
    setIsOpen(true);
  }, []);
  const closeChat = useCallback(() => setIsOpen(false), []);

  const value = useMemo(
    () => ({ isOpen, scope, openChat, closeChat }),
    [isOpen, scope, openChat, closeChat]
  );

  return <ChatLauncherContext.Provider value={value}>{children}</ChatLauncherContext.Provider>;
}

export function useChatLauncher(): ChatLauncherState {
  const ctx = useContext(ChatLauncherContext);
  if (!ctx) {
    throw new Error("useChatLauncher must be used within a ChatLauncherProvider");
  }
  return ctx;
}
