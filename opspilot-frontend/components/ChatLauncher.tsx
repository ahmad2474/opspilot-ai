"use client";

import { Suspense, useEffect, useState } from "react";
import { useSession } from "next-auth/react";
import ChatPanel from "@/components/ChatPanel";
import { useChatLauncher } from "@/components/ChatLauncherProvider";

// sessionStorage key gating the one-time "Need help?" hint (see the effect
// below) -- namespaced so it can't collide with anything else in the app.
const HINT_SEEN_KEY = "opspilot.chatHintSeen";

// Hand-drawn, stroke-based icons matching this app's established
// convention -- same 24x24 viewBox/stroke pattern as NavBar.tsx's nav-tab
// icons (GalaxyView.tsx's own `Glyph`/`StandaloneGlyph` icons are a
// sibling hand-rolled-SVG convention too, but use a different, relative
// "-5 -5 10 10" viewBox -- cited as prior art for "no icon library,
// inline SVG only," not an exact viewBox match). There's deliberately no
// icon library dependency (lucide-react etc. is NOT installed), so these
// are plain inline SVGs rather than a new package. `currentColor` picks
// up the caller's text color class.
function ChatBubbleIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      width="24"
      height="24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.7}
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M4 6.5A2.5 2.5 0 0 1 6.5 4h11A2.5 2.5 0 0 1 20 6.5v7A2.5 2.5 0 0 1 17.5 16H10l-4.5 4v-4H6.5A2.5 2.5 0 0 1 4 13.5v-7Z" />
      <circle cx={8.5} cy={10} r={0.9} fill="currentColor" stroke="none" />
      <circle cx={12} cy={10} r={0.9} fill="currentColor" stroke="none" />
      <circle cx={15.5} cy={10} r={0.9} fill="currentColor" stroke="none" />
    </svg>
  );
}

function CloseIcon({ size = 18 }: { size?: number }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <line x1={6} y1={6} x2={18} y2={18} />
      <line x1={18} y1={6} x2={6} y2={18} />
    </svg>
  );
}

// Tiny orbit glyph used as a brand mark in the panel header -- same "star +
// orbit" motif as ChatPanel.tsx's empty-state glyph and GalaxyView.tsx's own
// canvas, so the header reads as continuous with the rest of the chat
// surface rather than a generic bar with a title on it.
function OrbitMark() {
  return (
    <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth={1.3}>
      <ellipse cx={12} cy={12} rx={10} ry={4.2} opacity={0.45} transform="rotate(-25 12 12)" />
      <circle cx={12} cy={12} r={3} fill="currentColor" stroke="none" />
    </svg>
  );
}

// Roadmap Section 5: "Floating chat launcher (bottom-right), available
// from every tab, slides open a panel -- not a top-level tab." Rendered
// once in app/layout.tsx (inside Providers), so structurally it's present
// on every page -- including /login, which middleware.ts deliberately
// excludes from its auth-redirect matcher. SessionProvider only supplies
// session *data*, it doesn't gate rendering, so an explicit useSession()
// check below is what actually keeps the button/panel off the sign-in
// screen (and off the loading interstitial, so it doesn't flash on then
// off) -- same `session?.user?.email`-style gate NavBar.tsx uses. The
// backend's require_session remains the real enforcement boundary for the
// underlying chat call regardless of what renders client-side. Visual
// pattern (fixed right-edge panel, backdrop-blur, translucent dark
// surface, translateX slide) is copied from GalaxyView.tsx's own detail
// side panel (~line 1169-1184) -- `fixed` instead of `absolute` since this
// panel isn't inside GalaxyView's own `relative` canvas container.
export default function ChatLauncher() {
  const { status } = useSession();
  const { isOpen, scope, openChat, closeChat } = useChatLauncher();

  // One-time discoverability hint ("Need help? Ask me anything") near the
  // launcher button. ChatLauncher is mounted once in the root layout (see
  // the note above), so component state alone wouldn't re-trigger on
  // route changes -- but a full page reload would remount it. The
  // sessionStorage flag makes it truly one-time-per-session rather than
  // once-per-mount, so it doesn't nag on every refresh either. Hidden as
  // soon as the panel is opened, and auto-fades a few seconds after first
  // showing.
  const [showHint, setShowHint] = useState(false);

  useEffect(() => {
    if (status !== "authenticated") return;
    if (typeof window === "undefined") return;
    if (window.sessionStorage.getItem(HINT_SEEN_KEY)) return;

    window.sessionStorage.setItem(HINT_SEEN_KEY, "1");
    setShowHint(true);
    const timer = setTimeout(() => setShowHint(false), 5000);
    return () => clearTimeout(timer);
  }, [status]);

  useEffect(() => {
    if (isOpen) setShowHint(false);
  }, [isOpen]);

  if (status !== "authenticated") {
    return null;
  }

  return (
    <>
      {/* One-time discoverability hint. Sits just above the launcher button
          -- purely a toast, never intercepts clicks (pointer-events: none)
          so it can't get in the way of the button underneath it. */}
      <div
        className="pointer-events-none fixed bottom-24 right-6 z-40 flex items-center gap-1.5 rounded-full border border-border bg-surface/95 px-3.5 py-2 text-xs text-muted shadow-lg backdrop-blur transition-opacity duration-500"
        style={{ opacity: showHint ? 1 : 0 }}
      >
        <span className="h-1.5 w-1.5 rounded-full bg-accent" />
        Need help? Ask me anything
      </div>

      <button
        onClick={() => (isOpen ? closeChat() : openChat())}
        className="fixed bottom-6 right-6 z-40 flex h-14 w-14 items-center justify-center rounded-full border border-accent/40 bg-surface/95 text-accent shadow-lg backdrop-blur transition-transform hover:scale-105 hover:bg-accent/10"
        aria-label={isOpen ? "Close chat" : "Open chat"}
        aria-expanded={isOpen}
      >
        {isOpen ? <CloseIcon size={22} /> : <ChatBubbleIcon />}
      </button>

      {/* Outer frame: THIS component owns the fixed, bounded, right-edge
          panel -- a fixed-height container (`h-screen`, not `h-full`, since
          nothing above it in the tree constrains a percentage height) split
          into a header region and a body region. ChatPanel is handed the
          body region as a plain bounded box (`min-h-0` flex child) and
          fills it with `h-full flex flex-col` of its own -- it doesn't
          define or assume any height/border chrome itself. This is the
          fix for the recurring double-framing bug: there is now exactly
          ONE component (this one) deciding the panel's outer size/border/
          header, not two disagreeing about it. `aria-hidden` + swallowing
          pointer events while closed keeps the slid-away panel from
          trapping focus/clicks behind the rest of the page. */}
      <div
        className="fixed right-0 top-0 z-40 flex h-screen w-full flex-col border-l border-border bg-bg/97 backdrop-blur sm:w-[420px]"
        style={{
          transform: isOpen ? "translateX(0)" : "translateX(100%)",
          transition: "transform 0.3s ease",
        }}
        aria-hidden={!isOpen}
      >
        <div className="flex shrink-0 items-center justify-between border-b border-border px-4 py-3.5">
          <div className="flex items-center gap-2 text-accent">
            <OrbitMark />
            <span className="font-mono text-sm tracking-wide text-text">
              Ask OpsPilot<span className="text-muted">_AI</span>
            </span>
          </div>
          <button
            onClick={closeChat}
            className="flex items-center justify-center rounded-md p-1.5 text-muted transition-colors hover:bg-surfacealt hover:text-text"
            aria-label="Close chat panel"
            tabIndex={isOpen ? 0 : -1}
          >
            <CloseIcon />
          </button>
        </div>

        {/* Body: bounded box handed to ChatPanel. `min-h-0` is required so
            this flex-1 region can actually shrink to the space left under
            the header (rather than growing to fit ChatPanel's content and
            pushing the fixed h-screen container's total height, which
            would just move the overflow problem up one level instead of
            solving it). Only mounted while open, so ChatPanel's state
            (draft input, scroll position) naturally resets each time the
            panel is reopened -- matching its previous behavior. */}
        {isOpen && (
          <div className="min-h-0 flex-1">
            {/* ChatPanel reads ?about=/&label= via useSearchParams (deep-
                link fallback for the standalone /chat route) which Next.js
                requires a Suspense boundary for -- same pattern as
                app/chat/page.tsx. Here it's mostly moot since `scope`
                (the prop) takes precedence, but the hook still runs. */}
            <Suspense fallback={null}>
              <ChatPanel initialAbout={scope} />
            </Suspense>
          </div>
        )}
      </div>
    </>
  );
}
