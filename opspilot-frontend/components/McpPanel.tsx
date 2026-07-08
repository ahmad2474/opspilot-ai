"use client";

import { useCallback, useEffect, useState } from "react";
import { getMcpServerInfo, type McpServerInfo } from "@/lib/api";

export default function McpPanel() {
  const [info, setInfo] = useState<McpServerInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setInfo(await getMcpServerInfo());
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Couldn't reach the MCP server info endpoint — confirm the backend is running on port 8000."
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
          <h1 className="text-lg font-medium">MCP Server</h1>
          <p className="mt-1 max-w-2xl text-sm text-muted">
            Alongside the chat agent, this same read-only service layer is exposed as a{" "}
            <a
              href="https://modelcontextprotocol.io"
              target="_blank"
              rel="noreferrer"
              className="text-accent hover:underline"
            >
              Model Context Protocol
            </a>{" "}
            server (<span className="font-mono text-xs">app/mcp/server.py</span>) — any
            MCP-compatible client (Claude Desktop, another agent) can query this AWS account
            directly, over stdio JSON-RPC, without going through this web app at all. The list
            below is fetched live from the running server, not hand-maintained.
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

      {!error && loading && !info && (
        <div className="rounded-lg border border-border bg-surface p-6 text-sm text-muted">
          Loading MCP server info…
        </div>
      )}

      {!error && info && (
        <div>
          <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-3">
            <div className="rounded-lg border border-border bg-surface p-3">
              <div className="font-mono text-[11px] uppercase tracking-wide text-muted">
                Server name
              </div>
              <div className="mt-1 font-mono text-sm text-text">{info.server_name}</div>
            </div>
            <div className="rounded-lg border border-border bg-surface p-3">
              <div className="font-mono text-[11px] uppercase tracking-wide text-muted">
                Transport
              </div>
              <div className="mt-1 text-sm text-text">{info.transport}</div>
            </div>
            <div className="rounded-lg border border-border bg-surface p-3">
              <div className="font-mono text-[11px] uppercase tracking-wide text-muted">
                Tools exposed
              </div>
              <div className="mt-1 text-sm text-text">{info.tool_count}</div>
            </div>
          </div>

          <div className="mb-2 font-mono text-[11px] uppercase tracking-wide text-muted">
            Registered tools
          </div>
          <div className="space-y-2">
            {info.tools.map((tool) => (
              <div key={tool.name} className="rounded-lg border border-border bg-surface p-3">
                <div className="font-mono text-sm text-accent">{tool.name}</div>
                {tool.description && (
                  <div className="mt-1 text-xs text-muted">{tool.description}</div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
