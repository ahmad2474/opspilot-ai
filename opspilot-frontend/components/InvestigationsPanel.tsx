"use client";

import { useCallback, useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { getInvestigations, type Investigation } from "@/lib/api";

export default function InvestigationsPanel() {
  const [investigations, setInvestigations] = useState<Investigation[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await getInvestigations();
      setInvestigations(res.investigations);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Couldn't load investigations — confirm the backend is running on port 8000."
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
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h1 className="text-lg font-medium">Investigations</h1>
          <p className="mt-1 text-sm text-muted">
            Every chat investigation is embedded and persisted to DynamoDB. The agent
            searches this memory (via <span className="font-mono text-xs">find_similar_past_investigations</span>)
            when a question sounds like something that may have come up before.
          </p>
        </div>
        <button
          onClick={load}
          disabled={loading}
          className="rounded-md border border-border px-3 py-1.5 text-xs text-muted transition-colors hover:border-accent hover:text-text disabled:opacity-40"
        >
          {loading ? "Refreshing…" : "Refresh"}
        </button>
      </div>

      {error && (
        <div className="rounded-lg border border-status-bad/40 bg-status-bad/10 p-4 text-sm text-status-bad">
          {error}
        </div>
      )}

      {!error && loading && !investigations && (
        <div className="rounded-lg border border-border bg-surface p-6 text-sm text-muted">
          Loading investigation memory…
        </div>
      )}

      {!error && investigations && investigations.length === 0 && (
        <div className="rounded-lg border border-border bg-surface p-6 text-sm text-muted">
          No investigations recorded yet — ask a question in Chat and it&apos;ll show up here.
        </div>
      )}

      {!error && investigations && investigations.length > 0 && (
        <div className="space-y-3">
          {investigations.map((inv) => (
            <div key={inv.id} className="rounded-lg border border-border bg-surface p-4">
              <div className="mb-1 flex items-start justify-between gap-4">
                <div className="text-sm font-medium text-text">{inv.question}</div>
                <div className="whitespace-nowrap font-mono text-[11px] text-muted">
                  {new Date(inv.created_at).toLocaleString()}
                </div>
              </div>
              <div className="mb-1 text-xs italic text-muted">{inv.trace_summary}</div>
              <div className="prose prose-invert prose-sm max-w-none text-xs prose-table:text-xs prose-th:text-muted prose-td:align-top prose-code:before:content-none prose-code:after:content-none">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{inv.conclusion}</ReactMarkdown>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
