import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { RunOutcome, WorkflowTaskStatus } from "@/lib/types";

// One color per outcome, used by both the badge and the run timeline's bars so
// a bar and its row always read the same. As with StateBadge, color never
// carries the meaning alone — the badge pairs it with the word.
export const OUTCOME_COLOR: Record<RunOutcome, string> = {
  running: "bg-ok animate-pulse-dot",
  starting: "bg-info animate-pulse-dot",
  queued: "bg-info",
  stalled: "bg-warn-dot",
  succeeded: "bg-ok",
  failed: "bg-destructive",
  cancelled: "bg-faint",
};

/** The bar fill: same palette, minus the pulse (an animated bar is noise). */
export function outcomeFill(outcome: RunOutcome): string {
  return OUTCOME_COLOR[outcome]?.split(" ")[0] ?? "bg-faint";
}

export function RunBadge({ outcome, className }: { outcome: RunOutcome; className?: string }) {
  return (
    <Badge className={className}>
      <span className={cn("size-[7px] rounded-full", OUTCOME_COLOR[outcome] || "bg-faint")} />
      {outcome}
    </Badge>
  );
}

// Task states inside a workflow run — same palette family as OUTCOME_COLOR so
// a task chip and a run badge never disagree about what a color means.
export const TASK_COLOR: Record<WorkflowTaskStatus, string> = {
  pending: "bg-info",
  launching: "bg-info animate-pulse-dot",
  running: "bg-ok animate-pulse-dot",
  succeeded: "bg-ok",
  failed: "bg-destructive",
  skipped: "bg-faint",
};

export function TaskBadge({
  status,
  className,
}: {
  status: WorkflowTaskStatus;
  className?: string;
}) {
  return (
    <Badge className={className}>
      <span className={cn("size-[7px] rounded-full", TASK_COLOR[status] || "bg-faint")} />
      {status}
    </Badge>
  );
}
