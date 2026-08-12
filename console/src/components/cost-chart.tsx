"use client";

import { useRouter } from "next/navigation";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { cost, shortId } from "@/lib/format";
import type { SessionSummary } from "@/lib/types";

const TOP = 8;

export function CostChart({ sessions }: { sessions: SessionSummary[] | null }) {
  const router = useRouter();
  const rows = sessions
    ?.filter((s) => s.cost_usd > 0)
    .sort((a, b) => b.cost_usd - a.cost_usd)
    .slice(0, TOP)
    .map((s) => ({ id: s.id, label: shortId(s.id), cost: s.cost_usd }));

  return (
    <Card>
      <CardHeader>
        <CardTitle>Cost by session</CardTitle>
        <CardDescription>
          {rows && rows.length < (sessions?.length ?? 0)
            ? `Top ${rows.length} of the ${sessions?.length} most recent sessions`
            : `The ${sessions?.length ?? "—"} most recent sessions`}
        </CardDescription>
      </CardHeader>
      <CardContent>
        {rows === undefined ? (
          <Skeleton className="h-56 w-full" />
        ) : rows.length === 0 ? (
          <p className="py-16 text-center text-[13px] text-muted-foreground">
            No cost recorded yet.
          </p>
        ) : (
          <ResponsiveContainer width="100%" height={Math.max(120, rows.length * 32 + 30)}>
            <BarChart data={rows} layout="vertical" margin={{ top: 0, right: 12, bottom: 0, left: 8 }}>
              <CartesianGrid horizontal={false} stroke="var(--border)" />
              <XAxis
                type="number"
                tickFormatter={(v: number) => `$${v.toFixed(2)}`}
                tick={{ fill: "var(--muted-foreground)", fontSize: 11 }}
                axisLine={{ stroke: "var(--border)" }}
                tickLine={false}
              />
              <YAxis
                type="category"
                dataKey="label"
                width={110}
                tick={{ fill: "var(--muted-foreground)", fontSize: 11, fontFamily: "var(--font-mono)" }}
                axisLine={{ stroke: "var(--border)" }}
                tickLine={false}
              />
              <Tooltip
                cursor={{ fill: "var(--secondary)", opacity: 0.6 }}
                content={({ active, payload }) => {
                  if (!active || !payload?.length) return null;
                  const row = payload[0].payload as { id: string; cost: number };
                  return (
                    <div className="rounded-md border border-border bg-card px-2.5 py-1.5 font-mono text-xs shadow-sm">
                      <div>{row.id}</div>
                      <div className="text-muted-foreground">{cost(row.cost)}</div>
                    </div>
                  );
                }}
              />
              <Bar
                dataKey="cost"
                fill="var(--chart-1)"
                barSize={14}
                radius={[0, 4, 4, 0]}
                className="cursor-pointer"
                onClick={(row: { id?: string }) => row.id && router.push(`/session?sid=${row.id}`)}
              />
            </BarChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  );
}
