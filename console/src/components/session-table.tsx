"use client";

import { useRouter } from "next/navigation";
import { Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Skeleton } from "@/components/ui/skeleton";
import { StateBadge } from "@/components/state-badge";
import { cost, relTime, shortId } from "@/lib/format";
import type { SessionSummary } from "@/lib/types";

export function SessionTable({
  sessions,
  now,
  compact = false,
  emptyMessage = "No sessions yet — run a query and it will appear here.",
  onDelete,
}: {
  sessions: SessionSummary[] | null;
  now: number;
  compact?: boolean;
  emptyMessage?: string;
  onDelete?: (session: SessionSummary) => void;
}) {
  const router = useRouter();

  if (sessions === null) {
    return (
      <div className="space-y-2 p-3">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-8 w-full" />
        ))}
      </div>
    );
  }
  if (sessions.length === 0) {
    return (
      <p className="px-4 py-10 text-center text-[13px] text-muted-foreground">
        {emptyMessage}
      </p>
    );
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Session</TableHead>
          <TableHead>State</TableHead>
          {!compact && <TableHead>Model</TableHead>}
          {!compact && <TableHead>Workspace</TableHead>}
          <TableHead className="text-right">Cost</TableHead>
          {!compact && <TableHead className="text-right">Events</TableHead>}
          <TableHead className="text-right">Updated</TableHead>
          {onDelete && <TableHead className="w-8" />}
        </TableRow>
      </TableHeader>
      <TableBody>
        {sessions.map((s) => (
          <TableRow
            key={s.id}
            className="cursor-pointer"
            onClick={() => router.push(`/session?sid=${s.id}`)}
          >
            <TableCell className="font-mono text-xs" title={s.id}>
              {shortId(s.id)}
            </TableCell>
            <TableCell>
              <StateBadge state={s.state} />
            </TableCell>
            {!compact && (
              <TableCell className="font-mono text-xs text-muted-foreground">
                {s.model || "—"}
              </TableCell>
            )}
            {!compact && (
              <TableCell className="font-mono text-xs text-muted-foreground">
                {s.workspace || "—"}
              </TableCell>
            )}
            <TableCell className="text-right font-mono text-xs tabular-nums">
              {cost(s.cost_usd)}
            </TableCell>
            {!compact && (
              <TableCell className="text-right font-mono text-xs tabular-nums text-muted-foreground">
                {s.seq_head}
              </TableCell>
            )}
            <TableCell className="text-right font-mono text-xs text-muted-foreground">
              {relTime(s.updated_at ?? s.created_at, now) || "—"}
            </TableCell>
            {onDelete && (
              <TableCell className="w-8 text-right">
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-7 text-muted-foreground hover:text-destructive"
                  title={
                    s.state === "running"
                      ? "Kill the session before deleting it"
                      : `Delete ${s.id}`
                  }
                  disabled={s.state === "running"}
                  onClick={(e) => {
                    e.stopPropagation();
                    onDelete(s);
                  }}
                >
                  <Trash2 />
                </Button>
              </TableCell>
            )}
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
