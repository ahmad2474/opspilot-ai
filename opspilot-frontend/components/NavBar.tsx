"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const TABS = [
  { href: "/chat", label: "Chat" },
  { href: "/resources", label: "Resources" },
];

export default function NavBar() {
  const pathname = usePathname();

  return (
    <header className="border-b border-border bg-surface">
      <div className="mx-auto flex max-w-5xl items-center gap-8 px-6 py-4">
        <span className="font-mono text-sm tracking-wide text-accent">
          OPSPILOT<span className="text-muted">_AI</span>
        </span>
        <nav className="flex gap-1">
          {TABS.map((tab) => {
            const active = pathname?.startsWith(tab.href);
            return (
              <Link
                key={tab.href}
                href={tab.href}
                className={`rounded px-3 py-1.5 text-sm transition-colors ${
                  active
                    ? "bg-surfacealt text-text"
                    : "text-muted hover:text-text"
                }`}
              >
                {tab.label}
              </Link>
            );
          })}
        </nav>
      </div>
    </header>
  );
}
