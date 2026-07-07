"use client";

import { useState, useRef, useEffect } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { sendChatMessage, type TraceStep } from "@/lib/api";
import ReasoningTrace from "@/components/ReasoningTrace";

interface Message {
  role: "user" | "assistant" | "error";
  content: string;
  provider?: string;
  trace?: TraceStep[];
}

const SUGGESTIONS = [
  "What EC2 instances are running?",
  "Is any instance over 80% CPU?",
  "Is anything wrong with my EC2 instance?",
];

export default function ChatPanel() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
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

  return (
    <div className="flex h-[calc(100vh-9rem)] flex-col rounded-lg border border-border bg-surface">
      <div className="flex-1 overflow-y-auto p-6">
        {messages.length === 0 && (
          <div className="flex h-full flex-col items-center justify-center gap-4 text-center">
            <p className="text-sm text-muted">
              Ask about your AWS infrastructure. The agent has read-only access
              to EC2 and CloudWatch.
            </p>
            <div className="flex flex-wrap justify-center gap-2">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  onClick={() => handleSend(s)}
                  className="rounded-full border border-border px-3 py-1.5 text-xs text-muted transition-colors hover:border-accent hover:text-text"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}

        <div className="flex flex-col gap-4">
          {messages.map((m, i) => (
            <div key={i} className={m.role === "user" ? "self-end" : "self-start"}>
              <div
                className={`rounded-lg px-4 py-2.5 text-sm leading-relaxed ${
                  m.role === "user"
                    ? "max-w-xl bg-accent/90 text-bg"
                    : m.role === "error"
                      ? "max-w-xl border border-status-bad/40 bg-status-bad/10 text-status-bad"
                      : "max-w-4xl border border-border bg-surfacealt text-text"
                }`}
              >
                {m.role === "user" || m.role === "error" ? (
                  <div className="whitespace-pre-wrap">{m.content}</div>
                ) : (
                  <div className="prose prose-invert prose-sm max-w-none prose-table:text-xs prose-th:text-muted prose-td:align-top prose-code:before:content-none prose-code:after:content-none">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.content}</ReactMarkdown>
                  </div>
                )}
              </div>
              {m.provider && (
                <div className="mt-1 font-mono text-[11px] text-muted">
                  answered by {m.provider}
                </div>
              )}
              {m.trace && <ReasoningTrace steps={m.trace} />}
            </div>
          ))}
          {sending && (
            <div className="self-start rounded-lg border border-border bg-surfacealt px-4 py-2.5 text-sm text-muted">
              Investigating…
            </div>
          )}
        </div>
        <div ref={bottomRef} />
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          handleSend(input);
        }}
        className="flex gap-2 border-t border-border p-4"
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask about your infrastructure…"
          className="flex-1 rounded-md border border-border bg-surfacealt px-3 py-2 text-sm text-text placeholder:text-muted focus:border-accent"
          disabled={sending}
        />
        <button
          type="submit"
          disabled={sending || !input.trim()}
          className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-bg transition-opacity disabled:opacity-40"
        >
          Send
        </button>
      </form>
    </div>
  );
}
