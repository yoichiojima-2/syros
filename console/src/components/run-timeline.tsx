"use client";

import { Skeleton } from "@/components/ui/skeleton";
import { outcomeFill } from "@/components/run-badge";
import { clockTime, duration, shortId } from "@/lib/format";
import type { RunOutcome, WorkflowRun } from "@/lib/types";
import { cn } from "@/lib/utils";

// Enough bars to show a pattern, few enough that each stays readable.
const MAX_BARS = 40;
// A finished run that took no measurable time still happened: give every bar a
// floor so "instant" reads as a run rather than as a gap.
const MIN_BAR_PERCENT = 4;

/** Run history as one bar per workflow run — height is duration, color is status.
 *
 * Oldest on the left, so the newest run lands where the eye already is after
 * reading the table below. The axis is scaled to completed runs only, and a
 * run still going is hatched and clamped to the top of it: its duration is
 * still growing, and letting it set the scale would flatten every finished
 * run into the baseline the moment one run hangs. */
export function RunTimeline({
  runs,
  now,
  className,
}: {
  runs: WorkflowRun[] | null;
  now: number;
  className?: string;
}) {
  if (runs === null) return <Skeleton className={cn("h-40 w-full", className)} />;
  if (runs.length === 0) {
    return (
      <p className={cn("py-16 text-center text-[13px] text-muted-foreground", className)}>
        No runs yet.
      </p>
    );
  }

  const bars = runs.slice(0, MAX_BARS).reverse();
  const finished = (run: WorkflowRun) =>
    run.finished_at && run.started_at ? Math.max(0, run.finished_at - run.started_at) : null;
  const elapsed = (run: WorkflowRun) =>
    finished(run) ?? (run.started_at ? Math.max(0, now - run.started_at) : 0);
  const longest = Math.max(...bars.map((run) => finished(run) ?? 0), 1);

  return (
    <div className={className}>
      {/* min-width keeps every bar readable; on narrow screens the chart
          scrolls sideways instead of spilling out of its card
          (md:overflow-visible so hover tooltips aren't clipped on desktop) */}
      <div className="overflow-x-auto md:overflow-visible">
        <div className="flex items-end gap-3">
          <div className="flex h-40 flex-col justify-between py-px text-right font-mono text-[10px] text-faint">
            <span>{duration(longest)}</span>
            <span>0</span>
          </div>
          <div className="flex h-40 min-w-0 flex-1 items-end gap-[3px] border-b border-border">
            {bars.map((run) => {
              const seconds = elapsed(run);
              const pending = finished(run) === null;
              const tasks = Object.values(run.tasks);
              const done = tasks.filter((t) => t.status === "succeeded").length;
              return (
                <div
                  key={run.id}
                  aria-label={`${run.status} run ${run.id}`}
                  className="group relative min-w-[5px] flex-1 rounded-t-[3px]"
                  style={{
                    height: `${Math.max(
                      MIN_BAR_PERCENT,
                      (Math.min(seconds, longest) / longest) * 100,
                    )}%`,
                  }}
                >
                  <span
                    className={cn(
                      "block size-full rounded-t-[3px] transition-opacity group-hover:opacity-80",
                      outcomeFill(run.status as RunOutcome),
                      // a run in flight has no settled height yet; the stripes
                      // say "still growing" without animating the layout
                      pending && "opacity-70 [background-image:repeating-linear-gradient(45deg,transparent,transparent_3px,rgba(255,255,255,0.35)_3px,rgba(255,255,255,0.35)_6px)]",
                    )}
                  />
                  <span className="pointer-events-none absolute bottom-[calc(100%+6px)] left-1/2 z-10 hidden -translate-x-1/2 rounded-lg border border-border bg-card px-2.5 py-1.5 text-left font-mono text-[11px] whitespace-nowrap shadow-sm group-hover:block">
                    <span className="block">{shortId(run.id)}</span>
                    <span className="block text-muted-foreground">
                      {run.status} · {pending ? `${duration(seconds)}…` : duration(seconds)} ·{" "}
                      {done}/{tasks.length} tasks
                    </span>
                    <span className="block text-faint">{clockTime(run.started_at)}</span>
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      </div>
      <div className="mt-1.5 flex justify-between pl-12 text-[10px] text-faint">
        <span>{clockTime(bars[0]?.started_at ?? null)}</span>
        <span>
          {bars.length} of {runs.length} run{runs.length === 1 ? "" : "s"}
        </span>
        <span>{clockTime(bars[bars.length - 1]?.started_at ?? null)}</span>
      </div>
    </div>
  );
}
