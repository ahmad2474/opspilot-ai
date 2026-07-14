"use client";

import { useCallback, useEffect, useState } from "react";
import { getAuditLog, type AuditLogEntry } from "@/lib/api";

// Roadmap Section 5's "Audit Log" tab, backed by GET /audit-log
// (opspilot-backend/app/api/routes/audit_log.py). Read docs/SECURITY.md
// Section 7 before touching this file: real coverage today is exactly four
// action types (mcp_token_generated, mcp_token_revoked, login_success,
// login_failed) -- this table will typically be short for a single-admin
// app, so the empty/sparse states below are written to be honest about
// that rather than implying broader coverage exists.
const ACTION_LABEL: Record<string, string> = {
  mcp_token_generated: "MCP token generated",
  mcp_token_revoked: "MCP token revoked",
  login_success: "Login succeeded",
  login_failed: "Login failed",
};

// Trust-level badge, mirroring app/models/audit_log.py's own doc comment:
// actor_email is a cryptographically verified admin identity for the two
// mcp_token_* actions, but for login_failed specifically it's raw,
// unauthenticated, attacker-controllable input recorded as-is ("someone
// tried logging in as X and failed" is the useful signal) -- never proof
// X actually did anything. login_success sits in between: it's the email
// the (successful) login form submitted, verified only in the sense that
// the password check also passed for it.
function trustBadge(action: string): { text: string; className: string } | null {
  if (action === "mcp_token_generated" || action === "mcp_token_revoked") {
    return { text: "verified admin", className: "border-status-good/30 bg-status-good/10 text-status-good" };
  }
  if (action === "login_failed") {
    return {
      text: "unverified input",
      className: "border-status-bad/30 bg-status-bad/10 text-status-bad",
    };
  }
  if (action === "login_success") {
    return {
      text: "password-verified",
      className: "border-status-neutral/30 bg-status-neutral/10 text-status-neutral",
    };
  }
  return null;
}

export default function AuditLogPanel() {
  const [entries, setEntries] = useState<AuditLogEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await getAuditLog();
      setEntries(res.entries);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Couldn't load the audit log — confirm the backend is running on port 8000."
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div>
      <div className="mb-4 flex items-start justify-between gap-4">
        <div>
          <h1 className="text-lg font-medium">Audit Log</h1>
          <p className="mt-1 max-w-2xl text-sm text-muted">
            Currently records exactly four action types: MCP token generation/revocation, and
            login success/failure. This is not a comprehensive activity log yet -- dashboard reads
            (scans, resource lookups) and individual MCP tool calls are deliberately not written
            here (see <span className="font-mono text-xs">docs/SECURITY.md</span> Section 7).
          </p>
        </div>
        <button
          onClick={load}
          disabled={loading}
          className="whitespace-nowrap rounded-md border border-border px-3 py-1.5 text-xs text-muted transition-colors hover:border-accent hover:text-text disabled:opacity-40"
        >
          {loading ? "Refreshing…" : "Refresh"}
        </button>
      </div>

      {error && (
        <div className="rounded-lg border border-status-bad/40 bg-status-bad/10 p-4 text-sm text-status-bad">
          {error}
        </div>
      )}

      {!error && loading && !entries && (
        <div className="rounded-lg border border-border bg-surface p-6 text-sm text-muted">
          Loading audit log…
        </div>
      )}

      {!error && entries && entries.length === 0 && (
        <div className="rounded-lg border border-border bg-surface p-6 text-sm text-muted">
          No audit log entries yet.
        </div>
      )}

      {!error && entries && entries.length > 0 && (
        <div className="space-y-2">
          {entries.map((entry) => {
            const badge = trustBadge(entry.action);
            return (
              <div key={entry.id} className="rounded-lg border border-border bg-surface p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <span className="text-sm text-text">
                      {ACTION_LABEL[entry.action] ?? entry.action}
                    </span>
                    {badge && (
                      <span
                        className={`inline-block rounded-full border px-2 py-0.5 font-mono text-[10px] uppercase tracking-wide ${badge.className}`}
                      >
                        {badge.text}
                      </span>
                    )}
                  </div>
                  <span className="whitespace-nowrap font-mono text-[11px] text-muted">
                    {new Date(entry.created_at).toLocaleString()}
                  </span>
                </div>
                <div className="mt-1 font-mono text-xs text-muted">{entry.actor_email}</div>
                {entry.detail && <div className="mt-1 text-xs text-muted">{entry.detail}</div>}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
