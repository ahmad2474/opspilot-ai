"use client";

import type { ReactNode } from "react";
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { signOut, useSession } from "next-auth/react";

// Hand-drawn, stroke-based icons matching this app's established convention
// -- same 24x24 viewBox/stroke pattern as ChatLauncher.tsx's ChatBubbleIcon/
// CloseIcon: fill="none", stroke="currentColor", round linecap/linejoin.
// (GalaxyView.tsx's own Glyph/StandaloneGlyph icons use a different,
// relative "-5 -5 10 10" viewBox scaled by size -- a sibling hand-rolled-SVG
// convention in this codebase, not literally the same viewBox/coordinate
// system as this file's icons, so it's cited here as prior art for "no icon
// library, inline SVG only," not as an exact pattern match.) No icon
// library dependency (lucide-react etc. is NOT installed) -- these are
// plain inline SVGs.
// `currentColor` is deliberate, not decorative: it's what lets each icon
// pick up the *same* text-text/text-muted active-state color the tab's
// label already uses, just by sitting inside the same <Link>, with no
// separate active-state prop/logic needed here.
const ICON_SIZE = 14;
const ICON_PROPS = {
  viewBox: "0 0 24 24",
  width: ICON_SIZE,
  height: ICON_SIZE,
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.8,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
};

// Galaxy: globe -- outer circle + a vertical "meridian" ellipse + a
// horizontal "equator" line, the classic lat/long globe glyph called out
// in the mockup.
function GlobeIcon() {
  return (
    <svg {...ICON_PROPS}>
      <circle cx={12} cy={12} r={9} />
      <ellipse cx={12} cy={12} rx={4} ry={9} />
      <line x1={3} y1={12} x2={21} y2={12} />
    </svg>
  );
}

// Idle Resources: filter/list glyph -- three horizontal bars of decreasing
// width, the standard "filter" icon, matching the mockup's list/filter
// glyph.
function FilterIcon() {
  return (
    <svg {...ICON_PROPS}>
      <line x1={4} y1={6} x2={20} y2={6} />
      <line x1={7} y1={12} x2={17} y2={12} />
      <line x1={10} y1={18} x2={14} y2={18} />
    </svg>
  );
}

// Investigations: clock/history glyph -- circle face + hour/minute hands,
// matching the mockup's clock icon.
function ClockIcon() {
  return (
    <svg {...ICON_PROPS}>
      <circle cx={12} cy={12} r={9} />
      <line x1={12} y1={12} x2={12} y2={7} />
      <line x1={12} y1={12} x2={15.5} y2={13.5} />
    </svg>
  );
}

// Cost Overview: trending-up arrow -- rising polyline + arrowhead, matching
// the mockup's trending-up glyph.
function TrendingUpIcon() {
  return (
    <svg {...ICON_PROPS}>
      <polyline points="4,17 10,11 14,15 20,7" />
      <polyline points="14,7 20,7 20,13" />
    </svg>
  );
}

// Audit Log: not shown in the (cut-off) mockup -- a document/list glyph
// fits the same visual weight as the four confirmed icons above: a page
// outline with a few text-line strokes inside, read as "log entries".
function DocumentIcon() {
  return (
    <svg {...ICON_PROPS}>
      <rect x={6} y={3} width={12} height={18} rx={1.5} />
      <line x1={9} y1={8} x2={15} y2={8} />
      <line x1={9} y1={12} x2={15} y2={12} />
      <line x1={9} y1={16} x2={13} y2={16} />
    </svg>
  );
}

// Settings: not shown in the (cut-off) mockup -- a sliders glyph (three
// tracks, each with a knob at a different position) reads as "settings" at
// this same small size/stroke weight without the higher path complexity a
// true gear/cog outline would need.
function SlidersIcon() {
  return (
    <svg {...ICON_PROPS}>
      <line x1={4} y1={6} x2={20} y2={6} />
      <circle cx={9} cy={6} r={2} fill="currentColor" stroke="none" />
      <line x1={4} y1={12} x2={20} y2={12} />
      <circle cx={15} cy={12} r={2} fill="currentColor" stroke="none" />
      <line x1={4} y1={18} x2={20} y2={18} />
      <circle cx={11} cy={18} r={2} fill="currentColor" stroke="none" />
    </svg>
  );
}

// User: minimal person glyph -- head circle + a shoulders arc -- used for
// the account menu trigger in the top-right, following the exact same
// ICON_PROPS convention as every other icon in this file (not a filled
// avatar/photo style).
function UserIcon() {
  return (
    <svg {...ICON_PROPS}>
      <circle cx={12} cy={8} r={3.5} />
      <path d="M5 20c0-3.87 3.13-7 7-7s7 3.13 7 7" />
    </svg>
  );
}

// Post-ship nav restructure: Settings is no longer one of the tabs. Locked-
// in layout is now five tabs, in this order (Galaxy default/first) --
// Idle Resources, Investigations, Cost Overview, Audit Log -- plus two
// standalone icon buttons in the top-right, outside TABS entirely: a gear
// icon (reuses SlidersIcon, links directly to /settings, no dropdown) and
// a user-avatar icon (UserIcon, toggles a small popover with the signed-in
// email + Sign out). Idle Resources (components/IdleResourcesPanel.tsx)
// and Cost Overview (components/CostOverviewPanel.tsx) reuse the same
// scanRegion()/getRegions() data GalaxyView already fetches, filtered/
// aggregated client-side rather than a new backend endpoint; Audit Log
// (components/AuditLogPanel.tsx) is backed by the GET /audit-log route;
// Settings (components/SettingsPanel.tsx) has all three of the roadmap's
// sections (connected account, security posture, MCP access) and is still
// a fully built, directly-reachable route -- it just isn't promoted to a
// top-level tab anymore, matching the precedent already set below for
// /resources and /mcp.
//
// Two pages deliberately stay OUT of this list despite being fully built
// and reachable by direct URL, matching this app's existing precedent for
// Chat (see the note on ChatLauncher below):
//  - /resources (components/ResourcesPanel.tsx): EC2-only deep-dive cards
//    with CPU sparklines + an account-overview section (ServiceCards).
//    Real, non-duplicated value (per-instance CPU detail no scan-derived
//    view here provides) — kept reachable by URL, just not promoted to a
//    top-level tab since it's a narrower slice than Idle Resources/Cost
//    Overview now cover across all 15 scanned resource types.
//  - /mcp (components/McpPanel.tsx): roadmap 3.6 says outright "MCP itself
//    is not a top-level dashboard feature" — only its token lifecycle
//    belongs under Settings → MCP Access, which links to /mcp for the live
//    read-only tool list. The MCP Server tab was here before this cleanup;
//    removing it isn't a regression, it's correcting a gap-filler that
//    outlived the reason it was added.
// Chat is deliberately NOT a tab here -- roadmap Section 5 calls it out
// explicitly as a floating launcher (bottom-right, every page, slides open
// a panel), not a top-level tab. See components/ChatLauncher.tsx.
const TABS: { href: string; label: string; icon: ReactNode }[] = [
  { href: "/galaxy", label: "Galaxy", icon: <GlobeIcon /> },
  { href: "/idle-resources", label: "Idle Resources", icon: <FilterIcon /> },
  { href: "/investigations", label: "Investigations", icon: <ClockIcon /> },
  { href: "/cost-overview", label: "Cost Overview", icon: <TrendingUpIcon /> },
  { href: "/audit-log", label: "Audit Log", icon: <DocumentIcon /> },
];

// Visual-fidelity fix: the reference prototype (docs/aws-galaxy-dashboard.jsx)
// has no persistent nav bar at all -- its only chrome is small rounded
// translucent overlay cards floating directly on the continuous starfield,
// no hard edge-to-edge border cutting the scene. This nav previously had
// its own `border-b` and an independently-centered radial-gradient
// background, so on /galaxy it read as two visually separate stacked
// panels with a hard seam instead of one continuous scene. Both are
// deliberately dropped here (no hard border, no full-bleed custom
// background) -- the nav now just sits in the page's own bg-bg (see
// tailwind.config.ts), the same "floating card" language GalaxyView.tsx's
// own overlay cards use (rounded-lg border border-border bg-surface/90
// backdrop-blur), rather than a bolted-on generic top bar. #7fd7ff below is
// GalaxyView's COLOR_ACTIVE, used the same way it is there (a small
// "active" status dot); the amber accent is the existing `accent` Tailwind
// token, which already resolves to GalaxyView's COLOR_IDLE (#f0a202) --
// reused as-is rather than introducing a new hex. Stays in normal document
// flow (not fixed/absolute) -- every other page still applies its own
// max-w-6xl px-6 py-8 wrapper independently (see app/layout.tsx's comment).
export default function NavBar() {
  const pathname = usePathname();
  const { data: session } = useSession();

  // Account menu popover: self-contained outside-click-to-close, no shared
  // hook exists elsewhere in this codebase yet (GalaxyView.tsx's region
  // selector is the closest visual analog but only closes via each
  // option's own onClick, not on outside click). Standard useRef +
  // mousedown-listener pattern, scoped to this component.
  const [userMenuOpen, setUserMenuOpen] = useState(false);
  const userMenuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!userMenuOpen) return;
    function handleOutsideClick(event: MouseEvent) {
      if (userMenuRef.current && !userMenuRef.current.contains(event.target as Node)) {
        setUserMenuOpen(false);
      }
    }
    document.addEventListener("mousedown", handleOutsideClick);
    return () => document.removeEventListener("mousedown", handleOutsideClick);
  }, [userMenuOpen]);

  return (
    <header className="relative">
      <div className="flex items-center gap-8 px-6 py-4">
        <span className="flex items-center gap-2 font-mono text-sm tracking-wide text-accent">
          <span className="h-1.5 w-1.5 rounded-full" style={{ background: "#7fd7ff" }} />
          OPSPILOT<span className="text-muted">_AI</span>
        </span>
        <nav className="flex flex-1 gap-1">
          {TABS.map((tab) => {
            const active = pathname?.startsWith(tab.href);
            return (
              <Link
                key={tab.href}
                href={tab.href}
                className={`flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-sm backdrop-blur transition-colors ${
                  active
                    ? "border-accent/40 bg-accent/10 text-text"
                    : "border-transparent text-muted hover:border-border hover:bg-surface/60 hover:text-text"
                }`}
              >
                {tab.icon}
                {tab.label}
              </Link>
            );
          })}
        </nav>
        {session?.user?.email && (
          <div className="flex items-center gap-2">
            <Link
              href="/settings"
              aria-label="Settings"
              className="flex items-center justify-center rounded-md border border-border bg-surface/60 p-2 text-muted backdrop-blur transition-colors hover:border-accent hover:text-text"
            >
              <SlidersIcon />
            </Link>
            <div className="relative" ref={userMenuRef}>
              <button
                type="button"
                onClick={() => setUserMenuOpen((o) => !o)}
                aria-label="Account menu"
                aria-expanded={userMenuOpen}
                className="flex items-center justify-center rounded-md border border-border bg-surface/60 p-2 text-muted backdrop-blur transition-colors hover:border-accent hover:text-text"
              >
                <UserIcon />
              </button>
              {userMenuOpen && (
                <div className="absolute right-0 top-full z-50 mt-1 w-56 rounded-lg border border-border bg-surface/95 backdrop-blur">
                  <div className="break-all px-3 py-2 font-mono text-xs text-muted">
                    {session.user.email}
                  </div>
                  <button
                    type="button"
                    onClick={() => {
                      setUserMenuOpen(false);
                      signOut({ callbackUrl: "/login" });
                    }}
                    className="block w-full rounded-b-lg border-t border-border px-3 py-2 text-left text-xs text-muted transition-colors hover:bg-surfacealt hover:text-text"
                  >
                    Sign out
                  </button>
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </header>
  );
}
