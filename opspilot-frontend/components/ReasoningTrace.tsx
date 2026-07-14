"use client";

import { useState } from "react";
import type { TraceStep } from "@/lib/api";

function formatArgs(args: unknown): string {
  if (args == null) return "";
  if (typeof args === "object") return JSON.stringify(args);
  return String(args);
}

function formatOutput(output: unknown): string {
  if (output == null) return "";
  return JSON.stringify(output, null, 2);
}

export default function ReasoningTrace({ steps }: { steps: TraceStep[] }) {
  const [open, setOpen] = useState(false);

  if (steps.length === 0) return null;

  const toolCallCount = steps.filter((s) => s.type === "tool_call").length;

  return (
    <div className="mt-2 min-w-0">
      <button
        onClick={() => setOpen((o) => !o)}
        className="font-mono text-[11px] text-muted transition-colors hover:text-accent"
      >
        {open ? "▾" : "▸"} reasoning trace ({toolCallCount} tool call{toolCallCount === 1 ? "" : "s"})
      </button>

      {open && (
        <div className="mt-2 space-y-1.5 rounded-md border border-border bg-bg p-3 font-mono text-[11px] leading-relaxed">
          {steps.map((step, i) => {
            if (step.type === "message") {
              return (
                <div key={i} className="italic text-muted">
                  · {step.text}
                </div>
              );
            }
            if (step.type === "tool_call") {
              return (
                <div key={i} className="break-words text-accent">
                  → {step.tool}({formatArgs(step.arguments)})
                </div>
              );
            }
            return (
              <pre key={i} className="ml-3 overflow-x-auto whitespace-pre-wrap text-status-good">
                {formatOutput(step.output)}
              </pre>
            );
          })}
        </div>
      )}
    </div>
  );
}
