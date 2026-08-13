"use client";

import { useMemo, useState } from "react";
import { Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { SessionTable, deletable } from "@/components/session-table";
import { StateFilter } from "@/components/state-filter";
import { useAction, useNow, useSessions } from "@/lib/hooks";
import { post } from "@/lib/api";
import type { BulkDeleteResponse, SessionState, SessionSummary } from "@/lib/types";

export default function SessionsPage() {
  const sessions = useSessions();
  const now = useNow();
  const [state, setState] = useState<SessionState | null>(null);
  const [flash, run] = useAction();
  // Deleted ids drop from the table immediately; the 4s poll agrees once the
  // server-side delete lands.
  const [deleted, setDeleted] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);

  const filtered = useMemo(() => {
    if (sessions === null) return sessions;
    const alive = sessions.filter((s) => !deleted.has(s.id));
    return state === null ? alive : alive.filter((s) => s.state === state);
  }, [sessions, state, deleted]);

  // The selection is scoped to what's on screen and still deletable: a row
  // that gets filtered out, deleted, or picks up a live lease drops out of it
  // rather than silently riding along in the next bulk delete.
  const selectedIds = useMemo(
    () => (filtered ?? []).filter((s) => selected.has(s.id) && deletable(s)).map((s) => s.id),
    [filtered, selected],
  );

  const select = (ids: string[], value: boolean) =>
    setSelected((prev) => {
      const next = new Set(prev);
      for (const id of ids) {
        if (value) next.add(id);
        else next.delete(id);
      }
      return next;
    });

  const forget = (ids: string[]) => {
    setDeleted((prev) => new Set([...prev, ...ids]));
    select(ids, false);
  };

  const remove = (session: SessionSummary) => {
    if (!confirm(`Delete ${session.id}? The session and its history are removed permanently.`))
      return;
    run(async () => {
      await post(`/api/sessions/${session.id}/delete`);
      forget([session.id]);
      return "deleted";
    });
  };

  const removeSelected = () => {
    const ids = selectedIds;
    if (!ids.length || busy) return;
    const label = ids.length === 1 ? "1 session" : `${ids.length} sessions`;
    if (!confirm(`Delete ${label}? Every session and its history are removed permanently.`)) return;
    setBusy(true);
    run(async () => {
      try {
        const result = await post<BulkDeleteResponse>("/api/sessions/delete", { ids });
        forget(result.deleted);
        if (!result.failed.length) return `deleted ${result.deleted.length}`;
        // Partial success is the normal outcome when a selected session
        // started running mid-request — name the first reason, keep the rest
        // selected so a retry is one click.
        return `deleted ${result.deleted.length}, ${result.failed.length} failed — ${result.failed[0].error}`;
      } finally {
        setBusy(false);
      }
    });
  };

  return (
    <div className="min-h-0 flex-1 space-y-5 overflow-y-auto p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold tracking-tight">Sessions</h1>
        <StateFilter sessions={sessions} value={state} onChange={setState} />
      </div>
      {selectedIds.length > 0 && (
        <div className="flex flex-wrap items-center gap-3 rounded-lg border border-border bg-surface px-3 py-2">
          <span className="text-[13px] tabular-nums">
            {selectedIds.length} selected
          </span>
          <Button
            variant="destructive"
            size="sm"
            disabled={busy}
            onClick={removeSelected}
          >
            <Trash2 />
            {busy ? "Deleting…" : "Delete selected"}
          </Button>
          <Button variant="ghost" size="sm" onClick={() => setSelected(new Set())}>
            Clear
          </Button>
        </div>
      )}
      <Card>
        <CardContent className="px-2 py-2">
          <SessionTable
            sessions={filtered}
            now={now}
            onDelete={remove}
            selected={selected}
            onSelect={select}
            emptyMessage={
              state === null
                ? undefined
                : `No ${state} sessions.`
            }
          />
        </CardContent>
      </Card>
      {flash && <p className="text-center text-[11px] text-muted-foreground">{flash}</p>}
    </div>
  );
}
