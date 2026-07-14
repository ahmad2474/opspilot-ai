"use client";

import { useCallback, useEffect, useState } from "react";
import {
  generateMcpToken,
  getConnectedAccount,
  getMcpTokenStatus,
  revokeMcpToken,
  type AccountIdentity,
  type McpTokenStatus,
} from "@/lib/api";

// Roadmap Section 5's Settings tab spec has three bullets, in this order:
// connected account + IAM role ARN, security posture summary, and MCP
// Access (generate/revoke token, list of exposed tools). All three are now
// built here, in that order. The first two were originally deferred to a
// later hardening pass (this comment used to say so) -- that gap is what
// this section addition closes; MCP Access below is unchanged from when it
// first shipped.
export default function SettingsPanel() {
  const [status, setStatus] = useState<McpTokenStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [account, setAccount] = useState<AccountIdentity | null>(null);
  const [accountLoading, setAccountLoading] = useState(true);
  const [accountError, setAccountError] = useState<string | null>(null);

  const [actionError, setActionError] = useState<string | null>(null);
  const [actionPending, setActionPending] = useState<"generate" | "revoke" | null>(null);

  // Only ever set right after a successful "Generate" call, held in
  // component state (never persisted, never re-fetched from the backend --
  // GET /mcp/token/status never returns the plaintext). Leaving this page
  // or refreshing loses it for good, by design (roadmap 3.6: "shown
  // once").
  const [freshToken, setFreshToken] = useState<string | null>(null);
  // The backend's own copy for "this won't be shown again" (see
  // McpTokenGenerateResponse.warning) -- rendered verbatim rather than
  // re-typed here, so the two can't silently drift apart.
  const [freshTokenWarning, setFreshTokenWarning] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const loadStatus = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      setStatus(await getMcpTokenStatus());
    } catch (err) {
      setLoadError(
        err instanceof Error
          ? err.message
          : "Couldn't reach the backend to load MCP token status."
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadStatus();
  }, [loadStatus]);

  useEffect(() => {
    (async () => {
      setAccountLoading(true);
      setAccountError(null);
      try {
        setAccount(await getConnectedAccount());
      } catch (err) {
        setAccountError(
          err instanceof Error ? err.message : "Couldn't load the connected account."
        );
      } finally {
        setAccountLoading(false);
      }
    })();
  }, []);

  const handleGenerate = useCallback(async () => {
    setActionError(null);
    setActionPending("generate");
    setCopied(false);
    try {
      const result = await generateMcpToken();
      setFreshToken(result.token);
      setFreshTokenWarning(result.warning);
      await loadStatus();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Couldn't generate a token.");
    } finally {
      setActionPending(null);
    }
  }, [loadStatus]);

  const handleRevoke = useCallback(async () => {
    const confirmed = window.confirm(
      "Revoke the active MCP access token? Any connected MCP client (Claude Desktop, etc.) " +
        "will be rejected on its next tool call."
    );
    if (!confirmed) return;

    setActionError(null);
    setActionPending("revoke");
    try {
      await revokeMcpToken();
      setFreshToken(null);
      setFreshTokenWarning(null);
      await loadStatus();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Couldn't revoke the token.");
    } finally {
      setActionPending(null);
    }
  }, [loadStatus]);

  const handleCopy = useCallback(async () => {
    if (!freshToken) return;
    try {
      await navigator.clipboard.writeText(freshToken);
      setCopied(true);
    } catch {
      // Clipboard API can fail (permissions, non-secure context) -- the
      // token is still visible/selectable in the code block either way,
      // so this is non-fatal.
    }
  }, [freshToken]);

  return (
    <div>
      <h1 className="text-lg font-medium">Settings</h1>
      <p className="mt-1 max-w-2xl text-sm text-muted">
        Account and access configuration for this OpsPilot deployment.
      </p>

      <section className="mt-6">
        <h2 className="text-sm font-medium text-text">Connected account</h2>
        <p className="mt-1 max-w-2xl text-sm text-muted">
          This app uses a static AWS IAM user access key (not an assumed role), so there is no IAM
          role ARN to show here -- see &quot;Security posture summary&quot; below for the full
          explanation of that tradeoff.
        </p>

        {accountError && (
          <div className="mt-3 rounded-lg border border-status-bad/40 bg-status-bad/10 p-4 text-sm text-status-bad">
            {accountError}
          </div>
        )}

        {!accountError && accountLoading && (
          <div className="mt-3 rounded-lg border border-border bg-surface p-4 text-sm text-muted">
            Loading connected account…
          </div>
        )}

        {!accountError && !accountLoading && account && (
          <div className="mt-3 grid grid-cols-2 gap-3 sm:max-w-md">
            <div className="rounded-lg border border-border bg-surface p-3">
              <div className="font-mono text-[11px] uppercase tracking-wide text-muted">
                Account ID
              </div>
              <div className="mt-1 font-mono text-sm text-text">{account.account_id}</div>
            </div>
            <div className="rounded-lg border border-border bg-surface p-3">
              <div className="font-mono text-[11px] uppercase tracking-wide text-muted">
                Region
              </div>
              <div className="mt-1 font-mono text-sm text-text">{account.region}</div>
            </div>
          </div>
        )}
      </section>

      {/* Hand-authored, not a live backend call -- sourced directly from
          docs/SECURITY.md at the time this section was written. This will
          drift out of sync if SECURITY.md changes later and this component
          isn't updated alongside it -- same risk SECURITY.md itself flags
          in reverse ("kept in sync with docs/BUILD_PROGRESS.md"). If you
          change docs/SECURITY.md's Sections 2/3/6/7, re-check this section
          against it. */}
      <section className="mt-8">
        <h2 className="text-sm font-medium text-text">Security posture summary</h2>
        <div className="mt-3 space-y-3 text-sm text-muted">
          <div className="rounded-lg border border-border bg-surface p-3">
            <div className="font-mono text-[11px] uppercase tracking-wide text-muted">
              Authentication
            </div>
            <p className="mt-1 text-text">
              NextAuth (Credentials provider, single admin account). The frontend&apos;s login
              redirect is a UX nicety only -- every FastAPI route independently verifies the
              session token server-side via <code className="font-mono text-xs">require_session</code>,
              not just a frontend check.
            </p>
          </div>
          <div className="rounded-lg border border-accent/30 bg-accent/5 p-3">
            <div className="font-mono text-[11px] uppercase tracking-wide text-accent">
              AWS credentials -- accepted limitation
            </div>
            <p className="mt-1 text-text">
              This app currently uses a <strong>static, long-lived AWS IAM user access key</strong>{" "}
              (not short-lived assumed-role sessions). This is a deliberate, explicitly accepted
              gap for a local, single-admin, not-internet-facing tool -- it{" "}
              <strong>must be upgraded to short-lived assumed-role sessions</strong> before this
              app is ever hosted anywhere reachable by anyone other than its single operator, or
              used against an AWS account the operator isn&apos;t the sole person with access to.
            </p>
          </div>
          <div className="rounded-lg border border-border bg-surface p-3">
            <div className="font-mono text-[11px] uppercase tracking-wide text-muted">
              MCP token auth
            </div>
            <p className="mt-1 text-text">
              Bcrypt-hashed, single-active-token model. Required on every MCP tool call, including
              from localhost -- not skipped for local testing.
            </p>
          </div>
          <div className="rounded-lg border border-border bg-surface p-3">
            <div className="font-mono text-[11px] uppercase tracking-wide text-muted">
              Audit log coverage
            </div>
            <p className="mt-1 text-text">
              Exactly four action types today: MCP token generated/revoked, and login
              success/failure. Dashboard reads and individual MCP tool calls are not audit-logged
              yet -- see the{" "}
              <a href="/audit-log" className="text-accent hover:underline">
                Audit Log
              </a>{" "}
              tab.
            </p>
          </div>
          <p className="text-xs text-muted">
            Full security model, including every known limitation and its required-before
            condition: <code className="font-mono">docs/SECURITY.md</code> in the repo.
          </p>
        </div>
      </section>

      <section className="mt-8">
        <h2 className="text-sm font-medium text-text">MCP Access</h2>
        <p className="mt-1 max-w-2xl text-sm text-muted">
          Every{" "}
          <a href="/mcp" className="text-accent hover:underline">
            MCP server
          </a>{" "}
          tool call — from Claude Desktop or any other MCP-compatible client, over stdio
          JSON-RPC — is rejected unless it presents a valid access token, even for local testing
          (roadmap 3.6). Generate one below, then set it as the{" "}
          <code className="font-mono text-xs">OPSPILOT_MCP_TOKEN</code> environment variable for
          the process that runs the MCP server.
        </p>

        {loadError && (
          <div className="mt-3 rounded-lg border border-status-bad/40 bg-status-bad/10 p-4 text-sm text-status-bad">
            {loadError}
          </div>
        )}

        {!loadError && loading && (
          <div className="mt-3 rounded-lg border border-border bg-surface p-4 text-sm text-muted">
            Loading token status…
          </div>
        )}

        {!loadError && !loading && (
          <div className="mt-3 space-y-3">
            <div className="rounded-lg border border-border bg-surface p-4">
              <div className="flex items-center justify-between gap-4">
                <div>
                  <div className="font-mono text-[11px] uppercase tracking-wide text-muted">
                    Status
                  </div>
                  <div className="mt-1 flex items-center gap-2 text-sm">
                    <span
                      className={`inline-block h-2 w-2 rounded-full ${
                        status?.has_active_token ? "bg-status-good" : "bg-status-neutral"
                      }`}
                    />
                    <span className="text-text">
                      {status?.has_active_token ? "Active token" : "No active token"}
                    </span>
                  </div>
                  {status?.created_at && (
                    <div className="mt-1 text-xs text-muted">
                      Generated {new Date(status.created_at).toLocaleString()}
                    </div>
                  )}
                  {status?.revoked_at && !status.has_active_token && (
                    <div className="mt-1 text-xs text-muted">
                      Revoked {new Date(status.revoked_at).toLocaleString()}
                    </div>
                  )}
                </div>
                <div className="flex shrink-0 gap-2">
                  <button
                    onClick={handleGenerate}
                    disabled={actionPending !== null}
                    className="whitespace-nowrap rounded-md border border-border px-3 py-1.5 text-xs text-muted transition-colors hover:border-accent hover:text-text disabled:opacity-40"
                  >
                    {actionPending === "generate" ? "Generating…" : "Generate token"}
                  </button>
                  <button
                    onClick={handleRevoke}
                    disabled={actionPending !== null || !status?.has_active_token}
                    className="whitespace-nowrap rounded-md border border-status-bad/40 px-3 py-1.5 text-xs text-status-bad transition-colors hover:bg-status-bad/10 disabled:opacity-40"
                  >
                    {actionPending === "revoke" ? "Revoking…" : "Revoke"}
                  </button>
                </div>
              </div>
              {status?.has_active_token && (
                <p className="mt-3 text-xs text-muted">
                  Generating a new token immediately invalidates this one.
                </p>
              )}
            </div>

            {actionError && (
              <div className="rounded-lg border border-status-bad/40 bg-status-bad/10 p-4 text-sm text-status-bad">
                {actionError}
              </div>
            )}

            {freshToken && (
              <div className="rounded-lg border border-status-good/40 bg-status-good/10 p-4">
                <div className="text-sm font-medium text-status-good">
                  {freshTokenWarning}
                </div>
                <div className="mt-2 flex items-center gap-2">
                  <code className="flex-1 overflow-x-auto rounded border border-border bg-surfacealt px-2 py-1.5 font-mono text-xs text-text">
                    {freshToken}
                  </code>
                  <button
                    onClick={handleCopy}
                    className="whitespace-nowrap rounded-md border border-border px-3 py-1.5 text-xs text-muted transition-colors hover:border-accent hover:text-text"
                  >
                    {copied ? "Copied" : "Copy"}
                  </button>
                </div>
                <p className="mt-2 text-xs text-muted">
                  Set this as <code className="font-mono">OPSPILOT_MCP_TOKEN</code> for the MCP
                  server process (Claude Desktop&rsquo;s config, or this repo&rsquo;s own{" "}
                  <code className="font-mono">.env</code> for local testing). Navigating away from
                  this page discards it from view for good — generate a new token if you lose it
                  (this will invalidate the one above).
                </p>
              </div>
            )}

            <div>
              <div className="mb-2 font-mono text-[11px] uppercase tracking-wide text-muted">
                Exposed tools
              </div>
              <p className="text-sm text-muted">
                Every tool this token grants access to is listed live on the{" "}
                <a href="/mcp" className="text-accent hover:underline">
                  MCP Server
                </a>{" "}
                page.
              </p>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
