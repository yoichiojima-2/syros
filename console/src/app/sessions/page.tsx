"use client";

import { Card, CardContent } from "@/components/ui/card";
import { SessionTable } from "@/components/session-table";
import { useNow, useSessions } from "@/lib/hooks";

export default function SessionsPage() {
  const sessions = useSessions();
  const now = useNow();

  return (
    <div className="min-h-0 flex-1 space-y-5 overflow-y-auto p-6">
      <h1 className="font-serif text-2xl tracking-tight">Sessions</h1>
      <Card>
        <CardContent className="px-2 py-2">
          <SessionTable sessions={sessions} now={now} />
        </CardContent>
      </Card>
    </div>
  );
}
