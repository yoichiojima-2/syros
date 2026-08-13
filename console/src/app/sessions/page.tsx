"use client";

import { useMemo, useState } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { SessionTable } from "@/components/session-table";
import { StateFilter } from "@/components/state-filter";
import { useAction, useNow, useSessions } from "@/lib/hooks";
import { post } from "@/lib/api";
import type { SessionState, SessionSummary } from "@/lib/types";

export default function SessionsPage() {
  const sessions = useSessions();
  const now = useNow();
  const [state, setState] = useState<SessionState | null>(null);
  const [flash, run] = useAction();
  // Deleted ids drop from the table immediately; the 4s poll agrees once the
  // server-side delete lands.
  const [deleted, setDeleted] = useState<Set<string>>(new Set());

  const filtered = useMemo(() => {
    if (sessions === null) return sessions;
    const alive = sessions.filter((s) => !deleted.has(s.id));
    return state === null ? alive : alive.filter((s) => s.state === state);
  }, [sessions, state, deleted]);

  const remove = (session: SessionSummary) => {
    if (!confirm(`Delete ${session.id}? The session and its history are removed permanently.`))
      return;
    run(async () => {
      await post(`/api/sessions/${session.id}/delete`);
      setDeleted((prev) => new Set(prev).add(session.id));
      return "deleted";
    });
  };

  return (
    <div className="min-h-0 flex-1 space-y-5 overflow-y-auto p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="font-serif text-2xl tracking-tight">Sessions</h1>
        <StateFilter sessions={sessions} value={state} onChange={setState} />
      </div>
      <Card>
        <CardContent className="px-2 py-2">
          <SessionTable
            sessions={filtered}
            now={now}
            onDelete={remove}
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
