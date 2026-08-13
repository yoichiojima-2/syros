"use client";

import { useRouter } from "next/navigation";
import { Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
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

/** A running session holds a live lease, and a starting one has an execution
 * inbound — the server refuses to delete either, so the row is neither
 * selectable nor individually deletable. */
export function deletable(session: SessionSummary): boolean {
  return session.state !== "running" && session.state !== "starting";
}

export function SessionTable({
  sessions,
  now,
  compact = false,
  emptyMessage = "No sessions yet — run a query and it will appear here.",
  onDelete,
  selected,
  onSelect,
}: {
  sessions: SessionSummary[] | null;
  now: number;
  compact?: boolean;
  emptyMessage?: string;
  onDelete?: (session: SessionSummary) => void;
  // Selection is opt-in: pass both to get the checkbox column. onSelect takes
  // a batch so the header box is one call rather than one per row.
  selected?: ReadonlySet<string>;
  onSelect?: (ids: string[], value: boolean) => void;
}) {
  const router = useRouter();
  const selectable = selected !== undefined && onSelect !== undefined;

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

  const selectableIds = sessions.filter(deletable).map((s) => s.id);
  const selectedCount = selectableIds.filter((id) => selected?.has(id)).length;
  const allSelected = selectableIds.length > 0 && selectedCount === selectableIds.length;

  return (
    <Table>
      <TableHeader>
        <TableRow>
          {selectable && (
            <TableHead className="w-8">
              <Checkbox
                aria-label={allSelected ? "Clear selection" : "Select all deletable sessions"}
                title={allSelected ? "Clear selection" : "Select all deletable sessions"}
                checked={allSelected}
                indeterminate={selectedCount > 0 && !allSelected}
                disabled={selectableIds.length === 0}
                onChange={() => onSelect(selectableIds, !allSelected)}
              />
            </TableHead>
          )}
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
            {selectable && (
              <TableCell className="w-8" onClick={(e) => e.stopPropagation()}>
                <Checkbox
                  aria-label={`Select ${s.id}`}
                  title={deletable(s) ? `Select ${s.id}` : "Kill the session before deleting it"}
                  checked={selected.has(s.id)}
                  disabled={!deletable(s)}
                  onChange={(e) => onSelect([s.id], e.target.checked)}
                />
              </TableCell>
            )}
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
                    deletable(s) ? `Delete ${s.id}` : "Kill the session before deleting it"
                  }
                  disabled={!deletable(s)}
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
