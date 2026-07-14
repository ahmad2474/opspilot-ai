/**
 * NextAuth (Auth.js) configuration — single admin-style login, credentials
 * provider only (roadmap Section 3.5 / 2). No OAuth, no multi-user.
 *
 * Session strategy is JWT (no database needed for a single hardcoded
 * admin account). On top of NextAuth's own browser session, the `jwt`
 * callback mints a short-lived, separately-signed API token (`apiToken`)
 * that the frontend attaches as `Authorization: Bearer <token>` on every
 * call to the FastAPI backend. FastAPI verifies that token independently
 * (see opspilot-backend/app/core/security.py) — the frontend's session
 * cookie never crosses the network to the backend, only this token does.
 *
 * Why a separate token instead of forwarding the NextAuth session cookie:
 * NextAuth encrypts its JWT session cookie (JWE, A256GCM) which is not a
 * standard, easily-verifiable JWT from a non-Node backend. Minting our own
 * plain HS256 JWT with `jsonwebtoken`, signed with a secret shared between
 * the two services (AUTH_SHARED_SECRET), is the smallest amount of new
 * infrastructure that still gives FastAPI an independent, cryptographically
 * verifiable session check — no shared database/session store required.
 */
import type { NextAuthOptions } from "next-auth";
import CredentialsProvider from "next-auth/providers/credentials";
import bcrypt from "bcryptjs";
import jwt from "jsonwebtoken";
import crypto from "crypto";

const API_TOKEN_TTL_SECONDS = 60 * 60; // 1 hour — short-lived, refreshed automatically (see SessionProvider refetchInterval)

// Client-facing text for any "auth isn't configured" failure. Deliberately
// generic — naming the specific missing env var (e.g. ADMIN_PASSWORD_HASH)
// tells a caller exactly why sign-in is broken, which is free
// reconnaissance for no benefit (Step 7 security audit finding). The
// specific reason still goes to the server-side console.error right before
// this is thrown, for the admin's own debugging. Matches the wording used
// for the equivalent backend-side failure (see
// opspilot-backend/app/core/security.py's AUTH_UNAVAILABLE_MESSAGE).
const AUTH_UNAVAILABLE_MESSAGE = "Authentication is unavailable — please try again later.";

function getAuthSharedSecret(): string {
  const secret = process.env.AUTH_SHARED_SECRET;
  if (!secret) {
    throw new Error(
      "AUTH_SHARED_SECRET is not set — required to sign API tokens for the FastAPI backend."
    );
  }
  return secret;
}

function signApiToken(email: string): string {
  return jwt.sign({ sub: email }, getAuthSharedSecret(), {
    algorithm: "HS256",
    expiresIn: API_TOKEN_TTL_SECONDS,
  });
}

// --- Login audit event (roadmap Section 4 / Step 7) -------------------------
//
// authorize() below is where login success/failure is actually determined,
// server-side in Next.js, before any session JWT exists — so it can't call
// the FastAPI backend's normal Authorization: Bearer flow (there's nothing
// to send yet). Instead it signs {action, email, ts} with the same
// AUTH_SHARED_SECRET already used above to mint the API token, but as a
// plain HMAC-SHA256 signature rather than a JWT, and POSTs that signed
// payload to a small unauthenticated-looking-but-actually-signature-gated
// backend endpoint (POST /auth/login-audit). FastAPI verifies the signature
// plus a short timestamp freshness window (replay protection) — see
// opspilot-backend/app/core/security.py's verify_login_event_signature —
// so it can trust the caller is really this Next.js server process holding
// the shared secret, not an arbitrary internet client spoofing audit
// entries. The secret itself is never sent, only a signature derived from it.
//
// recordLoginAudit() is called WITHOUT `await` from authorize() below --
// genuinely fire-and-forget, not just failure-tolerant. Deliberately not
// awaited so a slow/unreachable backend can't tack extra latency onto the
// critical sign-in path (it's wrapped in try/catch so it still can't ever
// throw into that path either way). This is safe here specifically because
// this app runs as a long-lived Node process (`next start`, see
// package.json's "start" script and README's Docker Compose / local-only
// deployment notes) -- not a serverless/edge target that could tear the
// process down the instant the HTTP response is sent, which would risk
// killing this promise before the fetch completes. If this app is ever
// deployed to a serverless/edge platform, revisit this (either await with a
// short timeout, or use that platform's background-work primitive, e.g.
// Vercel's `waitUntil`).
const LOGIN_AUDIT_MESSAGE_SEPARATOR = ":";
const LOGIN_AUDIT_FETCH_TIMEOUT_MS = 3000;
// RFC 5321-ish bound, mirrors LoginAuditRequest.email's max_length in
// opspilot-backend/app/models/auth_event.py -- keep both sides in sync.
// The attempted email is attacker-controlled input (a valid signature is
// trivially obtainable by submitting the real login form), so truncate it
// client-side before it's ever sent rather than relying solely on the
// backend to reject an oversized payload.
const MAX_LOGIN_AUDIT_EMAIL_LENGTH = 320;

type LoginAuditAction = "login_success" | "login_failed";

function signLoginEvent(action: LoginAuditAction, email: string, ts: number): string {
  const message = [action, email, ts].join(LOGIN_AUDIT_MESSAGE_SEPARATOR);
  return crypto.createHmac("sha256", getAuthSharedSecret()).update(message).digest("hex");
}

async function recordLoginAudit(action: LoginAuditAction, email: string): Promise<void> {
  try {
    const ts = Math.floor(Date.now() / 1000);
    const signature = signLoginEvent(action, email, ts);
    const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), LOGIN_AUDIT_FETCH_TIMEOUT_MS);
    try {
      await fetch(`${apiBaseUrl}/auth/login-audit`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, email, ts, signature }),
        signal: controller.signal,
      });
    } finally {
      clearTimeout(timeoutId);
    }
  } catch (err) {
    // Audit logging must never block or fail the actual login flow — same
    // non-blocking spirit as the backend's own audit-write try/except (see
    // opspilot-backend/app/api/routes/mcp_auth.py). This catch also means
    // the promise returned by this function never rejects, so callers that
    // intentionally don't `await` it (see below) can't produce an
    // unhandled promise rejection.
    console.error("login_audit_write_failed", err);
  }
}

export const authOptions: NextAuthOptions = {
  session: {
    strategy: "jwt",
    maxAge: 30 * 24 * 60 * 60, // 30 days browser session; the short-lived apiToken is what actually gates the backend
  },
  pages: {
    signIn: "/login",
  },
  providers: [
    CredentialsProvider({
      name: "Credentials",
      credentials: {
        email: { label: "Email", type: "email" },
        password: { label: "Password", type: "password" },
      },
      async authorize(credentials) {
        const adminEmail = process.env.ADMIN_EMAIL;
        const adminPasswordHash = process.env.ADMIN_PASSWORD_HASH;

        // Best-effort attempted email for audit purposes — recorded even
        // when nothing else about the request can be trusted yet, since
        // "someone tried logging in as X and failed" is useful signal for
        // a single-admin app. Falls back to a placeholder rather than
        // silently skipping the audit write when the email field itself is
        // missing/empty.
        const attemptedEmail = (
          credentials?.email?.trim() || "(no email submitted)"
        ).slice(0, MAX_LOGIN_AUDIT_EMAIL_LENGTH);

        if (!adminEmail || !adminPasswordHash) {
          // The specific misconfiguration detail is useful for the admin's
          // own debugging but must not go in a client-facing error message
          // (Step 7 security audit finding) — log it here, throw generic.
          console.error(
            "ADMIN_EMAIL / ADMIN_PASSWORD_HASH are not configured on the server."
          );
          throw new Error(AUTH_UNAVAILABLE_MESSAGE);
        }
        if (!credentials?.email || !credentials?.password) {
          void recordLoginAudit("login_failed", attemptedEmail);
          return null;
        }

        const emailMatches =
          credentials.email.trim().toLowerCase() === adminEmail.trim().toLowerCase();
        if (!emailMatches) {
          void recordLoginAudit("login_failed", attemptedEmail);
          return null;
        }

        const passwordMatches = await bcrypt.compare(credentials.password, adminPasswordHash);
        if (!passwordMatches) {
          void recordLoginAudit("login_failed", attemptedEmail);
          return null;
        }

        void recordLoginAudit("login_success", adminEmail);
        return { id: adminEmail, email: adminEmail, name: "Admin" };
      },
    }),
  ],
  callbacks: {
    async jwt({ token }) {
      if (token.email) {
        // Refresh the API token's expiry on every session check so an
        // active browser tab never has its backend access silently expire.
        token.apiToken = signApiToken(token.email);
      }
      return token;
    },
    async session({ session, token }) {
      if (session.user) {
        session.user.email = token.email ?? session.user.email;
      }
      session.apiToken = typeof token.apiToken === "string" ? token.apiToken : undefined;
      return session;
    },
  },
};
