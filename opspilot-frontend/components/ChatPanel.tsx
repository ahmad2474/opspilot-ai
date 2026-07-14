"use client";

import { useState, useRef, useEffect } from "react";
import { useSearchParams } from "next/navigation";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { sendChatMessage, type TraceStep } from "@/lib/api";
import ReasoningTrace from "@/components/ReasoningTrace";

interface RecalledInvestigation {
  question: string;
  similarity: number;
}

interface Message {
  role: "user" | "assistant" | "error";
  content: string;
  provider?: string;
  trace?: TraceStep[];
  recalledFrom?: RecalledInvestigation | null;
}

// Spans the agent's actual tool coverage (orchestrator.py's TOOLS list —
// EC2/S3/Lambda/RDS/DynamoDB/SNS plus generic check_idle/estimate_cost/
// scan_region/list_resources tools covering all 15 roadmap resource
// types), not just EC2. Keeping this list EC2-only was a stale-copy bug —
// the same class of drift flagged before in orchestrator.py's own system
// prompt (docs/BUILD_PROGRESS.md). Do not narrow it back down.
const SUGGESTIONS = [
  "What's idle in this account?",
  "What's my projected monthly spend?",
  "List my S3 buckets",
  "Is anything running that looks unusual?",
];

function extractRecall(trace: TraceStep[]): RecalledInvestigation | null {
  const step = trace.find(
    (s) => s.type === "tool_result" && s.tool === "find_similar_past_investigations"
  );
  const results = (step?.output as { results?: { question: string; similarity: number }[] })
    ?.results;
  if (!results || results.length === 0) return null;
  return { question: results[0].question, similarity: results[0].similarity };
}

export interface ChatPanelAbout {
  id: string;
  label: string;
}

// Custom renderers for the assistant's markdown output. This is where the
// "trace the full width chain" discipline actually lives: a GFM table has
// no overflow handling of its own (remark-gfm just emits a plain <table>),
// and a fenced code block's content has no natural break points, so both
// get their own `overflow-x-auto` scroll boundary right here — scoped to
// the table/code block itself, not the whole bubble — instead of letting
// wide content push the bubble (and the fixed-width panel around it) wider.
// `Components` (from react-markdown) gives the destructured props their
// types via contextual typing, so this satisfies `strict`/noImplicitAny
// without resorting to `any`.
const markdownComponents: Components = {
  table: ({ children, ...props }) => (
    <div className="my-1 overflow-x-auto rounded-md border border-border/70">
      <table {...props}>{children}</table>
    </div>
  ),
  pre: ({ children, ...props }) => (
    <pre {...props} className="overflow-x-auto rounded-md border border-border/70 bg-bg/70 p-2.5">
      {children}
    </pre>
  ),
  code: ({ children, ...props }) => (
    <code {...props} className="[overflow-wrap:anywhere]">
      {children}
    </code>
  ),
  a: ({ children, ...props }) => (
    <a {...props} className="break-all text-accent underline decoration-accent/40 underline-offset-2">
      {children}
    </a>
  ),
};

// Small inline icons for the composer/empty state — same hand-rolled,
// stroke-based, currentColor convention as NavBar.tsx's ICON_PROPS and
// ChatLauncher.tsx's icons. No icon library in this app.
function SendIcon() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
      <path d="M4.5 12 19.5 5l-5 15-3-6.5L4.5 12Z" />
      <path d="M11.5 13.5 19.5 5" />
    </svg>
  );
}

function RecallIcon() {
  return (
    <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 12a9 9 0 1 1 3 6.7" />
      <path d="M3 21v-5h5" />
    </svg>
  );
}

export default function ChatPanel({ initialAbout = null }: { initialAbout?: ChatPanelAbout | null } = {}) {
  const searchParams = useSearchParams();
  // "Ask about this resource" from the galaxy detail panel (roadmap
  // Section 5 / 3.8) pre-scopes chat. Two entry points feed this now:
  // the standalone /chat route (deep link/bookmark fallback) still reads
  // ?about=<id>&label=<name> from the URL, while the floating chat
  // launcher (rendered outside any route, see ChatLauncherProvider) passes
  // the same info via the `initialAbout` prop instead, since it isn't tied
  // to a URL. The prop takes precedence when both are present. We prefill
  // the input rather than auto-sending, so the user still confirms before
  // the agent runs any tool calls.
  const aboutId = initialAbout?.id ?? searchParams?.get("about") ?? null;
  const aboutLabel = initialAbout?.label ?? searchParams?.get("label") ?? aboutId;

  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState(
    aboutId ? `What's the status of ${aboutLabel} (${aboutId})?` : ""
  );
  const [sending, setSending] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  async function handleSend(text: string) {
    const trimmed = text.trim();
    if (!trimmed || sending) return;

    setMessages((prev) => [...prev, { role: "user", content: trimmed }]);
    setInput("");
    setSending(true);

    try {
      const res = await sendChatMessage(trimmed);
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: res.reply,
          provider: res.provider_used,
          trace: res.trace,
          recalledFrom: extractRecall(res.trace),
        },
      ]);
    } catch (err) {
      const detail = err instanceof Error ? err.message : "Unknown error";
      setMessages((prev) => [
        ...prev,
        {
          role: "error",
          content: `Couldn't reach the agent: ${detail}. Confirm the backend is running on port 8000.`,
        },
      ]);
    } finally {
      setSending(false);
    }
  }

  // NOTE on the outer container: this component is ONLY ever mounted
  // inside ChatLauncher.tsx's own fixed, bounded panel now (app/chat/page.tsx
  // just redirects into openChat() — it doesn't render <ChatPanel /> itself).
  // So ChatPanel does not own any outer chrome (no border/rounded box, no
  // vh-based height math) — it's a plain height-filling flex column that
  // trusts its parent to hand it a bounded box. `min-h-0` on both this root
  // and the scroll region below is what actually lets the message list
  // scroll internally instead of stretching the whole flex chain to fit its
  // content (a flex column's default `min-height: auto` would otherwise
  // block that).
  return (
    <div className="flex h-full min-h-0 flex-col">
      {aboutId && (
        <div className="shrink-0 border-b border-border bg-accent/10 px-4 py-2.5 text-xs leading-relaxed text-accent">
          Scoped from the galaxy view — asking about{" "}
          <span className="break-all font-mono">{aboutLabel}</span>
        </div>
      )}

      {/* Scroll region: the ONLY element in this tree allowed to scroll.
          `min-h-0` lets it actually shrink to the space left over after the
          scoped banner + footer (a flex-1 child's default min-height:auto
          would otherwise let its content push this panel's height instead
          of scrolling within it). `overflow-x-auto` (not `-hidden`) is the
          safety net here: `overflow-wrap`/`break-words` on a shrink-to-fit
          box (see the message column below, which uses `items-end`/
          `items-start`, not `stretch`) doesn't count toward that box's
          min-content width, so a bubble can still overflow even once every
          child does its own containment (min-w-0 on flex items,
          overflow-x-auto on tables/code/traces, [overflow-wrap:anywhere] on
          free text/prose). `-hidden` would silently clip that overflow with
          no way to reach it; `-auto` at least surfaces a scrollbar instead
          of eating content. */}
      <div className="min-h-0 flex-1 overflow-x-auto overflow-y-auto px-4 py-4">
        {messages.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-6 px-2 text-center">
            {/* Small orbit/planet glyph — ties the empty state to the
                galaxy view's cosmic language (GalaxyView.tsx's star + orbit
                motif) instead of reading as a generic blank chat widget.
                Purely decorative, currentColor via text-accent. */}
            <svg viewBox="0 0 48 48" width="48" height="48" className="text-accent">
              <circle cx={24} cy={24} r={3.5} fill="currentColor" opacity={0.15} />
              <ellipse
                cx={24}
                cy={24}
                rx={21}
                ry={8.5}
                fill="none"
                stroke="currentColor"
                strokeWidth={1.2}
                opacity={0.35}
                transform="rotate(-22 24 24)"
              />
              <ellipse
                cx={24}
                cy={24}
                rx={21}
                ry={8.5}
                fill="none"
                stroke="currentColor"
                strokeWidth={1.2}
                opacity={0.2}
                transform="rotate(22 24 24)"
              />
              <circle cx={24} cy={24} r={6.5} fill="currentColor" opacity={0.9} />
              <circle cx={44} cy={15} r={2} fill="currentColor" opacity={0.7} />
              <circle cx={6} cy={33} r={1.3} fill="currentColor" opacity={0.5} />
            </svg>
            <div className="space-y-1.5">
              <p className="text-sm font-medium text-text">Ask about your AWS account</p>
              <p className="max-w-xs text-xs leading-relaxed text-muted">
                Broad, read-only visibility across compute, storage, database,
                and networking resources — the agent can look things up but
                can&rsquo;t make changes.
              </p>
            </div>
            <div className="flex max-w-sm flex-wrap justify-center gap-2">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => handleSend(s)}
                  className="rounded-full border border-accent/30 bg-accent/5 px-3 py-1.5 text-xs text-muted transition-colors hover:border-accent hover:bg-accent/10 hover:text-text"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="flex flex-col gap-4">
            {messages.map((m, i) => {
              const isUser = m.role === "user";
              const isError = m.role === "error";
              return (
                <div key={i} className={`flex w-full ${isUser ? "justify-end" : "justify-start"}`}>
                  {/* Inner column groups the bubble with its metadata
                      (recall badge / provider badge / trace) so the outer
                      row's justify-end/justify-start only has to position
                      ONE flex item, with the metadata stacking underneath
                      the bubble instead of sitting beside it. `min-w-0`
                      overrides this column's default flex-item
                      `min-width: auto`, which is what actually lets
                      `max-w-[85%]` shrink it below the intrinsic width of
                      wide content (a table, a long unbroken string) instead
                      of that content forcing this column — and the row, and
                      the panel — wider. */}
                  <div className={`flex min-w-0 max-w-[85%] flex-col gap-1 ${isUser ? "items-end" : "items-start"}`}>
                    <div
                      className={`min-w-0 rounded-2xl px-3.5 py-2.5 text-sm leading-relaxed shadow-sm ${
                        isUser
                          ? "rounded-tr-sm bg-accent text-bg"
                          : isError
                            ? "rounded-tl-sm border border-status-bad/40 bg-status-bad/10 text-status-bad"
                            : "rounded-tl-sm border border-border bg-surfacealt text-text"
                      }`}
                    >
                      {isUser || isError ? (
                        <div className="min-w-0 whitespace-pre-wrap [overflow-wrap:anywhere]">{m.content}</div>
                      ) : (
                        <div className="prose prose-invert prose-sm min-w-0 max-w-none [overflow-wrap:anywhere] prose-p:my-1.5 prose-headings:my-2 prose-table:my-1 prose-table:text-xs prose-th:text-muted prose-td:align-top prose-li:my-0.5 prose-code:before:content-none prose-code:after:content-none">
                          <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
                            {m.content}
                          </ReactMarkdown>
                        </div>
                      )}
                    </div>

                    {m.recalledFrom && (
                      <div className="flex min-w-0 items-start gap-1 px-1 font-mono text-[11px] text-accent">
                        <RecallIcon />
                        <span className="min-w-0 [overflow-wrap:anywhere]">
                          recalled past investigation ({Math.round(m.recalledFrom.similarity * 100)}%
                          match): &ldquo;{m.recalledFrom.question}&rdquo;
                        </span>
                      </div>
                    )}
                    {m.provider && (
                      <div className="px-1 font-mono text-[11px] text-muted">answered by {m.provider}</div>
                    )}
                    {m.trace && <ReasoningTrace steps={m.trace} />}
                  </div>
                </div>
              );
            })}

            {sending && (
              <div className="flex w-full justify-start">
                <div className="flex items-center gap-1.5 rounded-2xl rounded-tl-sm border border-border bg-surfacealt px-4 py-3">
                  <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-accent [animation-delay:-0.2s]" />
                  <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-accent [animation-delay:-0.1s]" />
                  <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-accent" />
                  <span className="ml-1.5 text-xs text-muted">Investigating…</span>
                </div>
              </div>
            )}
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          handleSend(input);
        }}
        className="flex shrink-0 items-center gap-2 border-t border-border bg-surface px-3 py-3"
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask about your infrastructure…"
          className="min-w-0 flex-1 rounded-full border border-border bg-surfacealt px-4 py-2.5 text-sm text-text placeholder:text-muted focus:border-accent"
          disabled={sending}
        />
        <button
          type="submit"
          disabled={sending || !input.trim()}
          className="flex shrink-0 items-center gap-1.5 rounded-full bg-accent px-4 py-2.5 text-sm font-medium text-bg transition-opacity disabled:opacity-40"
        >
          <SendIcon />
          Send
        </button>
      </form>
    </div>
  );
}
