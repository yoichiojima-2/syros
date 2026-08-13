"use client";

import { STATE_DOT } from "@/components/state-badge";
import { cn } from "@/lib/utils";
import type { SessionState, SessionSummary } from "@/lib/types";

const ORDER: SessionState[] = ["running", "queued", "stalled", "idle", "terminated", "unknown"];

export function StateFilter({
  sessions,
  value,
  onChange,
}: {
  sessions: SessionSummary[] | null;
  value: SessionState | null;
  onChange: (state: SessionState | null) => void;
}) {
  if (sessions === null || sessions.length === 0) return null;

  const counts = new Map<SessionState, number>();
  for (const s of sessions) counts.set(s.state, (counts.get(s.state) ?? 0) + 1);
  // Only offer states that actually occur, so the bar stays short.
  const states = ORDER.filter((st) => counts.has(st));
  if (states.length < 2) return null;

  const chip =
    "inline-flex cursor-pointer items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[11px] whitespace-nowrap transition-colors";

  return (
    <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Filter by state">
      <button
        type="button"
        aria-pressed={value === null}
        onClick={() => onChange(null)}
        className={cn(
          chip,
          value === null
            ? "border-foreground/30 bg-secondary text-secondary-foreground"
            : "border-border bg-surface text-muted-foreground hover:text-foreground",
        )}
      >
        all
        <span className="tabular-nums opacity-60">{sessions.length}</span>
      </button>
      {states.map((st) => (
        <button
          key={st}
          type="button"
          aria-pressed={value === st}
          onClick={() => onChange(value === st ? null : st)}
          className={cn(
            chip,
            value === st
              ? "border-foreground/30 bg-secondary text-secondary-foreground"
              : "border-border bg-surface text-muted-foreground hover:text-foreground",
          )}
        >
          <span className={cn("size-[7px] rounded-full", STATE_DOT[st])} />
          {st}
          <span className="tabular-nums opacity-60">{counts.get(st)}</span>
        </button>
      ))}
    </div>
  );
}
