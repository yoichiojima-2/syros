"use client";

import { Suspense } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { ArrowLeft, Pause, Play, Trash2, Zap } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { RunBadge } from "@/components/run-badge";
import { RunTimeline } from "@/components/run-timeline";
import { StatCard } from "@/components/stat-card";
import { useAction, useNow, useDeployment } from "@/lib/hooks";
import { post } from "@/lib/api";
import { clockTime, compact, cost, duration, relTime, shortId, untilTime } from "@/lib/format";
import type { RunSummary, DeploymentSummary } from "@/lib/types";

// Run history for one deployment: what it is, when it fires next, and how every
// run so far went. The runs are ordinary sessions — each row opens its
// transcript — so this page adds a lens, not a second source of truth.

/** The run options actually set on the deployment, rendered for the badge row.
 *  The stored dict carries every serializable AgentOptions field, most of them
 *  null on any given deployment; showing those would bury the two that matter. */
function setOptions(options: Record<string, unknown>): [string, string][] {
  return Object.entries(options).flatMap(([key, value]): [string, string][] => {
    if (value === null || value === undefined || value === "" || value === false) return [];
    if (Array.isArray(value)) return value.length ? [[key, value.join(" ")]] : [];
    if (typeof value === "object") {
      const entries = Object.entries(value as Record<string, unknown>);
      return entries.length ? [[key, entries.map(([k, v]) => `${k}=${v}`).join(" ")]] : [];
    }
    return [[key, compact(value, 60)]];
  });
}

export default function DeploymentPage() {
  // useSearchParams requires a Suspense boundary under static export
  return (
    <Suspense>
      <DeploymentInner />
    </Suspense>
  );
}

function DeploymentInner() {
  const name = useSearchParams().get("name");
  const router = useRouter();
  const { deployment, runs, missing, refresh } = useDeployment(name);
  const now = useNow();
  const [flash, act] = useAction();

  if (!name || missing) {
    return (
      <p className="pt-20 text-center text-[13px] text-muted-foreground">
        {name ? `No deployment named ${name}.` : "No deployment selected."}{" "}
        <Link href="/deployments" className="text-primary hover:underline">
          Back to deployments
        </Link>
      </p>
    );
  }

  const paused = deployment ? !deployment.enabled : false;
  const command = (fn: () => Promise<string>) =>
    act(async () => {
      const message = await fn();
      refresh();
      return message;
    });

  return (
    <div className="min-h-0 flex-1 space-y-5 overflow-y-auto p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <Link
            href="/deployments"
            className="flex items-center gap-1.5 text-[12px] text-muted-foreground hover:text-foreground"
          >
            <ArrowLeft className="size-3.5" />
            Deployments
          </Link>
          <h1 className="pt-1 font-mono text-2xl font-semibold tracking-tight">{name}</h1>
          <DeploymentLine deployment={deployment} now={now} />
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={!deployment}
            onClick={() =>
              command(async () => {
                const { session_id } = await post<{ session_id: string }>(
                  `/api/deployments/${encodeURIComponent(name)}/run`,
                );
                return `started ${session_id}`;
              })
            }
          >
            <Zap />
            Run now
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={!deployment}
            onClick={() =>
              command(async () => {
                await post(`/api/deployments/${encodeURIComponent(name)}/enabled`, {
                  enabled: paused,
                });
                return paused ? "resumed" : "paused";
              })
            }
          >
            {paused ? <Play /> : <Pause />}
            {paused ? "Resume" : "Pause"}
          </Button>
          <Button
            variant="destructive"
            size="sm"
            disabled={!deployment}
            onClick={() => {
              if (!confirm(`Delete deployment ${name}? Its past runs are kept.`)) return;
              act(async () => {
                await post(`/api/deployments/${encodeURIComponent(name)}/delete`);
                router.push("/deployments");
                return "deleted";
              });
            }}
          >
            <Trash2 />
            Delete
          </Button>
        </div>
      </div>

      {deployment?.last_error && (
        <p className="rounded-lg border border-destructive/40 bg-card px-3 py-2 text-[12px] text-destructive">
          {deployment.last_error}
        </p>
      )}

      <Stats deployment={deployment} runs={runs} />

      <Card>
        <CardHeader>
          <CardTitle>Run history</CardTitle>
          <CardDescription>
            Bar height is wall-clock duration, color is outcome — click one to open its transcript
          </CardDescription>
        </CardHeader>
        <CardContent>
          <RunTimeline runs={runs} now={now} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Definition</CardTitle>
          <CardDescription>What every firing runs</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {deployment === null ? (
            <Skeleton className="h-20 w-full" />
          ) : (
            <>
              <pre className="chat-prose overflow-x-auto rounded-lg border border-border bg-surface px-3 py-2 font-mono text-[12px] whitespace-pre-wrap">
                {deployment.prompt}
              </pre>
              <div className="flex flex-wrap gap-1.5">
                {setOptions(deployment.options).map(([key, value]) => (
                  <Badge key={key} variant="secondary" className="font-mono">
                    {key}: {value}
                  </Badge>
                ))}
                {deployment.created_by && (
                  <Badge className="font-mono">created by {deployment.created_by}</Badge>
                )}
              </div>
            </>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Runs</CardTitle>
          <CardDescription>Newest first — every run is an ordinary session</CardDescription>
        </CardHeader>
        <CardContent className="px-2 pb-2">
          <RunsTable runs={runs} now={now} />
        </CardContent>
      </Card>
      {flash && <p className="text-center text-[11px] text-muted-foreground">{flash}</p>}
    </div>
  );
}

function DeploymentLine({ deployment, now }: { deployment: DeploymentSummary | null; now: number }) {
  if (!deployment) return <Skeleton className="mt-2 h-4 w-72" />;
  return (
    <p className="flex flex-wrap items-center gap-x-2 gap-y-1 pt-1.5 text-[13px] text-muted-foreground">
      <code className="font-mono text-foreground">{deployment.cron}</code>
      <span className="text-faint">·</span>
      <span className="font-mono">{deployment.timezone}</span>
      <span className="text-faint">·</span>
      {deployment.enabled ? (
        <span title={clockTime(deployment.next_run_at)}>
          next run {untilTime(deployment.next_run_at, now)}
        </span>
      ) : (
        <Badge>
          <span className="size-[7px] rounded-full bg-faint" />
          paused
        </Badge>
      )}
    </p>
  );
}

function Stats({
  deployment,
  runs,
}: {
  deployment: DeploymentSummary | null;
  runs: RunSummary[] | null;
}) {
  // Rates and averages only mean something over runs that finished; a run
  // still going has no duration and no verdict yet.
  const finished = runs?.filter((r) => r.duration_s !== null) ?? [];
  const decided = runs?.filter((r) => r.outcome !== "running" && r.outcome !== "queued") ?? [];
  const succeeded = decided.filter((r) => r.outcome === "succeeded").length;
  const totalCost = runs?.reduce((sum, r) => sum + r.cost_usd, 0);
  const meanDuration = finished.length
    ? finished.reduce((sum, r) => sum + (r.duration_s ?? 0), 0) / finished.length
    : null;

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
      <StatCard
        label="Last run"
        value={
          runs === null
            ? null
            : runs.length === 0
              ? "never"
              : (deployment?.last_run?.outcome ?? runs[0].outcome)
        }
        sub={runs?.length ? `${runs.length} in history` : "no runs yet"}
      />
      <StatCard
        label="Success rate"
        value={
          runs === null ? null : decided.length ? `${Math.round((succeeded / decided.length) * 100)}%` : "—"
        }
        sub={decided.length ? `${succeeded} of ${decided.length} finished runs` : "nothing finished yet"}
      />
      <StatCard
        label="Avg duration"
        value={runs === null ? null : meanDuration === null ? "—" : duration(meanDuration)}
        sub={finished.length ? `over ${finished.length} completed runs` : "no completed runs"}
      />
      <StatCard
        label="Total spend"
        value={totalCost === undefined ? null : cost(totalCost)}
        sub={
          deployment && deployment.skips > 0
            ? `${deployment.skips} slot${deployment.skips === 1 ? "" : "s"} skipped (run still active)`
            : "across the runs shown"
        }
      />
    </div>
  );
}

function RunsTable({ runs, now }: { runs: RunSummary[] | null; now: number }) {
  const router = useRouter();
  if (runs === null) {
    return (
      <div className="space-y-2 p-3">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-8 w-full" />
        ))}
      </div>
    );
  }
  if (runs.length === 0) {
    return (
      <p className="px-4 py-10 text-center text-[13px] text-muted-foreground">
        No runs yet — the next firing, or “Run now”, will start one.
      </p>
    );
  }
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Session</TableHead>
          <TableHead>Outcome</TableHead>
          <TableHead>Trigger</TableHead>
          <TableHead>Started</TableHead>
          <TableHead className="text-right">Duration</TableHead>
          <TableHead className="text-right">Cost</TableHead>
          <TableHead className="text-right">Events</TableHead>
          <TableHead>Stop reason</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {runs.map((run) => (
          <TableRow
            key={run.id}
            className="cursor-pointer"
            onClick={() => router.push(`/session?sid=${run.id}`)}
          >
            <TableCell className="font-mono text-xs" title={run.id}>
              {shortId(run.id)}
            </TableCell>
            <TableCell>
              <RunBadge outcome={run.outcome} />
            </TableCell>
            <TableCell className="text-xs text-muted-foreground">{run.trigger}</TableCell>
            <TableCell className="text-xs text-muted-foreground" title={clockTime(run.created_at)}>
              {relTime(run.created_at, now)}
            </TableCell>
            <TableCell className="text-right font-mono text-xs tabular-nums">
              {/* a live run has no final duration; count up from its start */}
              {duration(run.duration_s ?? (run.created_at ? now - run.created_at : null))}
            </TableCell>
            <TableCell className="text-right font-mono text-xs tabular-nums">
              {cost(run.cost_usd)}
            </TableCell>
            <TableCell className="text-right font-mono text-xs tabular-nums text-muted-foreground">
              {run.seq_head}
            </TableCell>
            <TableCell className="font-mono text-[11px] text-muted-foreground">
              {run.stop_reason || "—"}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
