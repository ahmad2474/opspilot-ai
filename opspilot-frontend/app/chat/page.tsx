"use client";

import { Suspense, useEffect } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useChatLauncher } from "@/components/ChatLauncherProvider";

// `/chat` is kept only as a deep-link fallback (e.g. bookmarked or
// externally-linked ?about=<id>&label=<name> URLs) -- the floating
// ChatLauncher (mounted once, globally, in app/layout.tsx) is the sole
// intended chat surface per roadmap Section 5 ("not a top-level tab").
// Rendering a second, standalone <ChatPanel /> here used to produce two
// simultaneous ChatPanel mounts (this page's inline one plus the
// launcher's overlay) whenever a signed-in user visited /chat directly.
// Instead, this page now just opens the launcher's panel (pre-scoped via
// the same ?about=/&label= params, if present) and redirects into a real
// page so nothing renders twice.
function ChatRedirect() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { openChat } = useChatLauncher();

  useEffect(() => {
    const about = searchParams?.get("about") ?? null;
    const label = searchParams?.get("label") ?? null;

    if (about) {
      // Mirror ChatPanel's own prop-vs-searchParams label fallback
      // (label ?? id) so behavior stays consistent across both entry
      // points.
      openChat({ id: about, label: label ?? about });
    } else {
      openChat();
    }

    router.replace("/galaxy");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return null;
}

export default function ChatPage() {
  return (
    <Suspense fallback={null}>
      <ChatRedirect />
    </Suspense>
  );
}
