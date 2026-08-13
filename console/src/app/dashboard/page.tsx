"use client";

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

const DAYS = 14;
const STATE_ORDER: SessionState[] = [
  "running",
  "queued",
  "stalled",
  "idle",
  "terminated",
  "unknown",
];

interface DayBucket {
  label: string;
  count: number;
  cost: number;
}

// Bucket sessions into the last DAYS calendar days (browser-local), oldest first.
function byDay(sessions: SessionSummary[]): DayBucket[] {
  const buckets = new Map<string, DayBucket>();
  const day = new Date();
  day.setHours(0, 0, 0, 0);
  day.setDate(day.getDate() - (DAYS - 1));
  for (let i = 0; i < DAYS; i++) {
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

  const active = sessions?.filter((s) => ACTIVE_STATES.has(s.state)).length ?? 0;
  const totalCost = sessions?.reduce((sum, s) => sum + s.cost_usd, 0);
  const billed = sessions?.filter((s) => s.cost_usd > 0).length || 0;
  const events = sessions?.reduce((sum, s) => sum + s.seq_head, 0);
  const days = sessions ? byDay(sessions) : null;
  const scope = `across the ${sessions?.length ?? 0} most recent sessions`;

  return (
    <div className="min-h-0 flex-1 space-y-5 overflow-y-auto p-6">
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
          label="Transcript events"
          value={events === undefined ? null : events.toLocaleString()}
          sub={scope}
        />
      </div>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <DailyChart
          title="Sessions per day"
          description={`Started in the last ${DAYS} days, ${scope}`}
          data={days?.map((d) => ({ label: d.label, value: d.count })) ?? null}
          tickFormat={(v) => String(v)}
          format={(v) => `${v} session${v === 1 ? "" : "s"}`}
          integer
        />
        <DailyChart
          title="Spend per day"
          description={`By session start date, ${scope}`}
          data={days?.map((d) => ({ label: d.label, value: d.cost })) ?? null}
          tickFormat={(v) => `$${v.toFixed(2)}`}
          format={cost}
        />
      </div>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <StateBreakdown sessions={sessions} />
        <ModelChart sessions={sessions} />
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
}: {
  title: string;
  description: string;
  data: { label: string; value: number }[] | null;
  tickFormat: (v: number) => string;
  format: (v: number) => string;
  integer?: boolean;
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
            Nothing in the last {DAYS} days.
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
                interval={1}
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

function ModelChart({ sessions }: { sessions: SessionSummary[] | null }) {
  const byModel = sessions
    ? sessions.reduce((acc, s) => {
        const model = s.model ?? "unknown";
        const row = acc.get(model) ?? { model, cost: 0, count: 0 };
        row.cost += s.cost_usd;
        row.count += 1;
        return acc.set(model, row);
      }, new Map<string, { model: string; cost: number; count: number }>())
    : null;
  const rows = byModel && [...byModel.values()].sort((a, b) => b.cost - a.cost).slice(0, 8);
  const anyCost = rows?.some((r) => r.cost > 0);
  return (
    <Card>
      <CardHeader>
        <CardTitle>Cost by model</CardTitle>
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
                dataKey="model"
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
                  const row = payload[0].payload as { model: string; cost: number; count: number };
                  return (
                    <ChartTooltip
                      label={row.model}
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
