import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { RunOutcome } from "@/lib/types";

// One color per outcome, used by both the badge and the run timeline's bars so
// a bar and its row always read the same. As with StateBadge, color never
// carries the meaning alone — the badge pairs it with the word.
export const OUTCOME_COLOR: Record<RunOutcome, string> = {
  running: "bg-ok animate-pulse-dot",
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
