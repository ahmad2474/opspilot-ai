"use client";

import type { Session } from "next-auth";
import { SessionProvider } from "next-auth/react";
import { ChatLauncherProvider } from "@/components/ChatLauncherProvider";

export default function Providers({
  children,
  session,
}: {
  children: React.ReactNode;
  session: Session | null;
}) {
  return (
    // `session` is hydrated server-side in app/layout.tsx via
    // getServerSession() and passed straight through here -- this is what
    // lets useSession() resolve to its final status immediately on first
    // render instead of starting in "loading" and waiting on a client-side
    // fetch to /api/auth/session, which used to cost a few seconds before
    // NavBar's settings/user icons and ChatLauncher could show. refetchInterval
    // keeps the short-lived apiToken (see lib/auth.ts) fresh for tabs left
    // open — otherwise it would only refresh on navigation. ChatLauncherProvider
    // lives inside SessionProvider so ChatLauncher (see components/ChatLauncher.tsx,
    // mounted once in app/layout.tsx) can read session status via useSession().
    // Note SessionProvider only supplies session *data* -- it does not itself
    // gate rendering, and /login is deliberately outside middleware.ts's
    // protected-route matcher. The actual "don't show the launcher when
    // signed out" gate is the explicit useSession() status check inside
    // ChatLauncher itself; the backend's require_session is the real
    // enforcement boundary for the underlying chat call either way.
    <SessionProvider session={session} refetchInterval={5 * 60}>
      <ChatLauncherProvider>{children}</ChatLauncherProvider>
    </SessionProvider>
  );
}
