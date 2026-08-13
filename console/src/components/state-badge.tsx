import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { SessionState } from "@/lib/types";

// State is never carried by color alone: the badge always pairs dot + label.
export const STATE_DOT: Record<SessionState, string> = {
  running: "bg-ok animate-pulse-dot",
  stalled: "bg-warn-dot",
  queued: "bg-info",
  idle: "bg-faint",
  terminated: "bg-destructive",
  unknown: "bg-faint",
};

export function StateBadge({ state, className }: { state: SessionState; className?: string }) {
  return (
    <Badge className={className}>
      <span className={cn("size-[7px] rounded-full", STATE_DOT[state] || "bg-faint")} />
      {state}
    </Badge>
  );
}
