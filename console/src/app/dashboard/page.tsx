"use client";

import { useState } from "react";
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
import { StatCard } from "@/components/stat-card";
import { STATE_DOT } from "@/components/state-badge";
import { cost } from "@/lib/format";
import { useSessions } from "@/lib/hooks";
import { ACTIVE_STATES, type SessionState, type SessionSummary } from "@/lib/types";
import { cn } from "@/lib/utils";

type RangeKey = "24h" | "7d" | "14d";

const RANGES: { key: RangeKey; label: string; days?: number }[] = [
  { key: "24h", label: "24h" },
  { key: "7d", label: "7d", days: 7 },
  { key: "14d", label: "14d", days: 14 },
];

const STATE_ORDER: SessionState[] = [
  "running",
  "starting",
  "queued",
  "stalled",
  "idle",
  "terminated",
  "unknown",
];

interface Bucket {
  label: string;
  count: number;
  cost: number;
}

// Bucket sessions into the selected range (browser-local), oldest first:
// hourly buckets for the last 24 hours, or calendar-day buckets otherwise.
function bucketize(sessions: SessionSummary[], range: RangeKey): Bucket[] {
  const buckets = new Map<string, Bucket>();
  const days = RANGES.find((r) => r.key === range)?.days;
  if (days === undefined) {
    const hour = new Date();
    hour.setMinutes(0, 0, 0);
    hour.setHours(hour.getHours() - 23);
    for (let i = 0; i < 24; i++) {
      buckets.set(hour.toISOString(), {
        label: `${String(hour.getHours()).padStart(2, "0")}:00`,
        count: 0,
        cost: 0,
      });
      hour.setHours(hour.getHours() + 1);
    }
    for (const s of sessions) {
      if (!s.created_at) continue;
      const t = new Date(s.created_at * 1000);
      t.setMinutes(0, 0, 0);
      const bucket = buckets.get(t.toISOString());
      if (bucket) {
        bucket.count += 1;
        bucket.cost += s.cost_usd;
      }
    }
    return [...buckets.values()];
  }
  const day = new Date();
  day.setHours(0, 0, 0, 0);
  day.setDate(day.getDate() - (days - 1));
  for (let i = 0; i < days; i++) {
    buckets.set(day.toDateString(), {
      label: `${day.getMonth() + 1}/${day.getDate()}`,
      count: 0,
      cost: 0,
    });
    day.setDate(day.getDate() + 1);
  }
  for (const s of sessions) {
    if (!s.created_at) continue;
    const bucket = buckets.get(new Date(s.created_at * 1000).toDateString());
    if (bucket) {
      bucket.count += 1;
      bucket.cost += s.cost_usd;
    }
  }
  return [...buckets.values()];
}

export default function DashboardPage() {
  const sessions = useSessions();
  const [range, setRange] = useState<RangeKey>("24h");
  const rangeLabel = range === "24h" ? "24 hours" : `${RANGES.find((r) => r.key === range)?.days} days`;

  const active = sessions?.filter((s) => ACTIVE_STATES.has(s.state)).length ?? 0;
  const totalCost = sessions?.reduce((sum, s) => sum + s.cost_usd, 0);
  const billed = sessions?.filter((s) => s.cost_usd > 0).length || 0;
  const events = sessions?.reduce((sum, s) => sum + s.seq_head, 0);
  const days = sessions ? bucketize(sessions, range) : null;
  const scope = `across the ${sessions?.length ?? 0} most recent sessions`;

  return (
    <div className="min-h-0 flex-1 space-y-5 overflow-y-auto p-4 sm:p-6">
      <h1 className="font-serif text-2xl tracking-tight">Dashboard</h1>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="Total spend"
          value={totalCost === undefined ? null : cost(totalCost)}
          sub={scope}
        />
        <StatCard
          label="Avg cost / session"
          value={
            totalCost === undefined ? null : cost(billed ? totalCost / billed : 0)
          }
          sub={billed ? `mean of the ${billed} sessions with recorded cost` : "no cost recorded yet"}
        />
        <StatCard
          label="Sessions"
          value={sessions === null ? null : String(sessions.length)}
          sub={active ? `${active} active now` : "none active now"}
          accent={!!active}
        />
        <StatCard
          label="Journal records"
          value={events === undefined ? null : events.toLocaleString()}
          sub={scope}
        />
      </div>
      <div className="flex items-center gap-1 rounded-lg border border-border bg-card p-0.5 w-fit">
        {RANGES.map((r) => (
          <button
            key={r.key}
            onClick={() => setRange(r.key)}
            className={cn(
              "rounded-md px-2.5 py-1 text-xs font-medium transition-colors",
              range === r.key
                ? "bg-secondary text-foreground"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            {r.label}
          </button>
        ))}
      </div>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <DailyChart
          title={range === "24h" ? "Sessions per hour" : "Sessions per day"}
          description={`Started in the last ${rangeLabel}, ${scope}`}
          data={days?.map((d) => ({ label: d.label, value: d.count })) ?? null}
          tickFormat={(v) => String(v)}
          format={(v) => `${v} session${v === 1 ? "" : "s"}`}
          integer
          range={range}
        />
        <DailyChart
          title={range === "24h" ? "Spend per hour" : "Spend per day"}
          description={`By session start time, ${scope}`}
          data={days?.map((d) => ({ label: d.label, value: d.cost })) ?? null}
          tickFormat={(v) => `$${v.toFixed(2)}`}
          format={cost}
          range={range}
        />
      </div>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <StateBreakdown sessions={sessions} />
        <CostBarChart
          sessions={sessions}
          title="Cost by model"
          keyOf={(s) => s.model ?? "unknown"}
        />
        <CostBarChart
          sessions={sessions}
          title="Cost by user"
          keyOf={(s) => s.created_by ?? "unknown"}
        />
      </div>
    </div>
  );
}

function ChartTooltip({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-border bg-card px-2.5 py-1.5 font-mono text-xs shadow-sm">
      <div>{label}</div>
      <div className="text-muted-foreground">{value}</div>
    </div>
  );
}

function DailyChart({
  title,
  description,
  data,
  tickFormat,
  format,
  integer,
  range,
}: {
  title: string;
  description: string;
  data: { label: string; value: number }[] | null;
  tickFormat: (v: number) => string;
  format: (v: number) => string;
  integer?: boolean;
  range: RangeKey;
}) {
  const empty = data !== null && data.every((d) => d.value === 0);
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <CardContent>
        {data === null ? (
          <Skeleton className="h-48 w-full" />
        ) : empty ? (
          <p className="py-16 text-center text-[13px] text-muted-foreground">
            Nothing in the last {range === "24h" ? "24 hours" : range === "7d" ? "7 days" : "14 days"}.
          </p>
        ) : (
          <ResponsiveContainer width="100%" height={192}>
            <BarChart data={data} margin={{ top: 4, right: 4, bottom: 0, left: -16 }}>
              <CartesianGrid vertical={false} stroke="var(--border)" />
              <XAxis
                dataKey="label"
                tick={{ fill: "var(--muted-foreground)", fontSize: 11 }}
                axisLine={{ stroke: "var(--border)" }}
                tickLine={false}
                interval={range === "24h" ? 3 : range === "7d" ? 0 : 1}
              />
              <YAxis
                allowDecimals={!integer}
                tickFormatter={tickFormat}
                tick={{ fill: "var(--muted-foreground)", fontSize: 11 }}
                axisLine={false}
                tickLine={false}
              />
              <Tooltip
                cursor={{ fill: "var(--secondary)", opacity: 0.6 }}
                content={({ active, payload }) => {
                  if (!active || !payload?.length) return null;
                  const row = payload[0].payload as { label: string; value: number };
                  return <ChartTooltip label={row.label} value={format(row.value)} />;
                }}
              />
              <Bar dataKey="value" fill="var(--chart-1)" maxBarSize={20} radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  );
}

function StateBreakdown({ sessions }: { sessions: SessionSummary[] | null }) {
  const rows = sessions
    ? STATE_ORDER.map((state) => ({
        state,
        count: sessions.filter((s) => s.state === state).length,
      })).filter((r) => r.count > 0)
    : null;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Sessions by state</CardTitle>
        <CardDescription>Derived liveness of the {sessions?.length ?? "—"} most recent sessions</CardDescription>
      </CardHeader>
      <CardContent>
        {rows === null ? (
          <Skeleton className="h-40 w-full" />
        ) : rows.length === 0 ? (
          <p className="py-16 text-center text-[13px] text-muted-foreground">No sessions yet.</p>
        ) : (
          <div className="space-y-3.5">
            {rows.map(({ state, count }) => (
              <div key={state}>
                <div className="flex items-center justify-between text-[13px]">
                  <span className="flex items-center gap-2">
                    {/* first class of STATE_DOT is the state's color; drop the pulse for fills */}
                    <span className={cn("size-[7px] rounded-full", STATE_DOT[state].split(" ")[0])} />
                    {state}
                  </span>
                  <span className="font-mono text-xs text-muted-foreground">{count}</span>
                </div>
                <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-secondary">
                  <div
                    className={cn("h-full rounded-full", STATE_DOT[state].split(" ")[0])}
                    style={{ width: `${(count / (sessions?.length || 1)) * 100}%` }}
                  />
                </div>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

/** Horizontal cost breakdown over the listed sessions, keyed however the
 *  caller likes — model and user share the one chart. */
function CostBarChart({
  sessions,
  title,
  keyOf,
}: {
  sessions: SessionSummary[] | null;
  title: string;
  keyOf: (s: SessionSummary) => string;
}) {
  const byKey = sessions
    ? sessions.reduce((acc, s) => {
        const key = keyOf(s);
        const row = acc.get(key) ?? { key, cost: 0, count: 0 };
        row.cost += s.cost_usd;
        row.count += 1;
        return acc.set(key, row);
      }, new Map<string, { key: string; cost: number; count: number }>())
    : null;
  const rows = byKey && [...byKey.values()].sort((a, b) => b.cost - a.cost).slice(0, 8);
  const anyCost = rows?.some((r) => r.cost > 0);
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <CardDescription>Spend {`across the ${sessions?.length ?? 0} most recent sessions`}</CardDescription>
      </CardHeader>
      <CardContent>
        {rows === null ? (
          <Skeleton className="h-40 w-full" />
        ) : !anyCost ? (
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
                dataKey="key"
                width={130}
                tickFormatter={(v: string) => (v.length > 18 ? v.slice(0, 17) + "…" : v)}
                tick={{ fill: "var(--muted-foreground)", fontSize: 11, fontFamily: "var(--font-mono)" }}
                axisLine={{ stroke: "var(--border)" }}
                tickLine={false}
              />
              <Tooltip
                cursor={{ fill: "var(--secondary)", opacity: 0.6 }}
                content={({ active, payload }) => {
                  if (!active || !payload?.length) return null;
                  const row = payload[0].payload as { key: string; cost: number; count: number };
                  return (
                    <ChartTooltip
                      label={row.key}
                      value={`${cost(row.cost)} · ${row.count} session${row.count === 1 ? "" : "s"}`}
                    />
                  );
                }}
              />
              <Bar dataKey="cost" fill="var(--chart-1)" barSize={16} radius={[0, 6, 6, 0]} />
            </BarChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  );
}
