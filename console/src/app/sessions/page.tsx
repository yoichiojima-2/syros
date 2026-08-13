"use client";

import { useMemo, useState } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { SessionTable } from "@/components/session-table";
import { StateFilter } from "@/components/state-filter";
import { useNow, useSessions } from "@/lib/hooks";
import type { SessionState } from "@/lib/types";

export default function SessionsPage() {
  const sessions = useSessions();
  const now = useNow();
  const [state, setState] = useState<SessionState | null>(null);

  const filtered = useMemo(() => {
    if (sessions === null || state === null) return sessions;
    return sessions.filter((s) => s.state === state);
  }, [sessions, state]);

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
            emptyMessage={
              state === null
                ? undefined
                : `No ${state} sessions.`
            }
          />
        </CardContent>
      </Card>
    </div>
  );
}
