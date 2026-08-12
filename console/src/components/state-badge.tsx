import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

// State is never carried by color alone: the badge always pairs dot + label.
const DOT: Record<string, string> = {
  running: "bg-ok animate-pulse-dot",
  stalled: "bg-warn-dot",
  queued: "bg-info",
  idle: "bg-faint",
  terminated: "bg-destructive",
};

export function StateBadge({ state, className }: { state: string; className?: string }) {
  return (
    <Badge className={className}>
      <span className={cn("size-[7px] rounded-full", DOT[state] || "bg-faint")} />
      {state}
    </Badge>
  );
}
