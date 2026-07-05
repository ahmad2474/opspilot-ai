const STATE_STYLES: Record<string, string> = {
  running: "bg-status-good/15 text-status-good border-status-good/30",
  stopped: "bg-status-neutral/15 text-status-neutral border-status-neutral/30",
  pending: "bg-accent/15 text-accent border-accent/30",
  stopping: "bg-accent/15 text-accent border-accent/30",
  "shutting-down": "bg-status-bad/15 text-status-bad border-status-bad/30",
  terminated: "bg-status-bad/15 text-status-bad border-status-bad/30",
};

export default function StatusBadge({ state }: { state: string }) {
  const style = STATE_STYLES[state] ?? "bg-surfacealt text-muted border-border";
  return (
    <span
      className={`inline-block rounded-full border px-2 py-0.5 font-mono text-[11px] uppercase tracking-wide ${style}`}
    >
      {state}
    </span>
  );
}
