"use client";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { StatCard } from "@/components/stat-card";
import { CostChart } from "@/components/cost-chart";
import { SessionTable } from "@/components/session-table";
import { useApprovals, useNow, useSessions } from "@/lib/hooks";
import { cost } from "@/lib/format";
import { ACTIVE_STATES } from "@/lib/types";

export default function OverviewPage() {
  const sessions = useSessions();
  const { approvals } = useApprovals();
  const now = useNow();

  const active = sessions?.filter((s) => ACTIVE_STATES.has(s.state)).length;
  const stalled = sessions?.filter((s) => s.state === "stalled").length || 0;
  const totalCost = sessions?.reduce((sum, s) => sum + s.cost_usd, 0);

  return (
    <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-5">
      <h1 className="text-lg font-semibold">Overview</h1>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <StatCard
          label="Active sessions"
          value={active === undefined ? null : String(active)}
          sub={stalled ? `${stalled} stalled` : "running, queued, or stalled"}
        />
        <StatCard
          label="Total cost"
          value={totalCost === undefined ? null : cost(totalCost)}
          sub={`across the ${sessions?.length ?? 0} most recent sessions`}
        />
        <StatCard
          label="Pending approvals"
          value={approvals === null ? null : String(approvals.length)}
          sub={approvals?.length ? "waiting on you" : "nothing waiting"}
          accent={!!approvals?.length}
        />
      </div>
      <CostChart sessions={sessions} />
      <Card>
        <CardHeader>
          <CardTitle>Recent sessions</CardTitle>
          <CardDescription>Newest first — click a row to open the transcript</CardDescription>
        </CardHeader>
        <CardContent className="px-2 pb-2">
          <SessionTable sessions={sessions?.slice(0, 8) ?? null} now={now} compact />
        </CardContent>
      </Card>
    </div>
  );
}
