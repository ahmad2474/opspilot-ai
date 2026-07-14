/**
 * Gate every route behind a valid NextAuth session (roadmap Section 3.5).
 * No session -> redirect to /login before any dashboard/data/API call fires.
 *
 * This is a UX nicety, not the real security boundary — FastAPI
 * independently re-validates a bearer token on every API call
 * (see opspilot-backend/app/core/security.py). Never rely on this
 * middleware alone to protect data.
 */
import { withAuth } from "next-auth/middleware";

export default withAuth({
  pages: {
    signIn: "/login",
  },
});

export const config = {
  // Protect everything except: the login page itself, NextAuth's own
  // /api/auth/* routes (or this creates a redirect loop), and Next.js
  // internals/static assets.
  matcher: ["/((?!login|api/auth|_next/static|_next/image|favicon.ico).*)"],
};
