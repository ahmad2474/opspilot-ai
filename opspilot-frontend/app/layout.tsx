import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import { getServerSession } from "next-auth";
import "./globals.css";
import NavBar from "@/components/NavBar";
import ChatLauncher from "@/components/ChatLauncher";
import Providers from "./providers";
import { authOptions } from "@/lib/auth";

// Font loaders must stay called at module scope (not inside the async
// RootLayout body below) -- Next.js 14.2's next/font relies on this.
const inter = Inter({ subsets: ["latin"], variable: "--font-inter" });
const jbmono = JetBrains_Mono({ subsets: ["latin"], variable: "--font-jbmono" });

export const metadata: Metadata = {
  title: "OpsPilot AI",
  description: "Agentic AWS infrastructure investigation assistant",
};

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  // Hydrate the session server-side so useSession() on the client doesn't
  // start in "loading" and have to round-trip to /api/auth/session before
  // NavBar's settings/user icons and ChatLauncher can resolve their final
  // state -- otherwise those icons visibly pop in a few seconds after the
  // rest of the page on every load. getServerSession returns null when
  // signed out (e.g. on /login), so that page's "no icons" behavior is
  // unaffected -- this only changes how fast the true state is known.
  const session = await getServerSession(authOptions);

  return (
    <html lang="en" className={`${inter.variable} ${jbmono.variable}`}>
      <body className="min-h-screen bg-bg font-sans text-text">
        <Providers session={session}>
          <NavBar />
          {/* No max-w/padding here on purpose -- /galaxy needs full viewport
              width for its starfield canvas (roadmap Section 5 layout), while
              every other page keeps its own centered max-w-6xl column by
              applying those classes itself in its own page.tsx. Don't add
              constraints back here without re-checking /galaxy. */}
          <main>{children}</main>
          {/* Floating chat launcher (roadmap Section 5) -- available from
              every tab, not a top-level nav tab. Rendered once here so it
              persists across route changes; ChatLauncherProvider (see
              app/providers.tsx) supplies the open/close/scope state that
              GalaxyView's "Ask about this resource" button also reaches
              into via useChatLauncher(). */}
          <ChatLauncher />
        </Providers>
      </body>
    </html>
  );
}
