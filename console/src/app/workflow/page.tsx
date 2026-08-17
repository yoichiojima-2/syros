"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { ArrowLeft, ArrowRight, Network, Pause, Pencil, Play, Trash2, Zap } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Select } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { RunBadge, TaskBadge } from "@/components/run-badge";
import { RunTimeline } from "@/components/run-timeline";
import { WorkflowGraph, type GraphTask } from "@/components/workflow-graph";
import { StatCard } from "@/components/stat-card";
import { WorkflowForm } from "@/components/workflow-form";
import { useAction, useNow, useWorkflow } from "@/lib/hooks";
import { post } from "@/lib/api";
import { clockTime, compact, duration, relTime, shortId, untilTime } from "@/lib/format";
import { cn } from "@/lib/utils";
import type {
  WorkflowRun,
  WorkflowSummary,
  WorkflowTaskSpec,
  WorkflowTaskState,
} from "@/lib/types";

// Run history for one workflow: what its tasks are, when it fires next, and
// how every run so far went. Task runs are ordinary sessions — each task chip
// opens its transcript — so this page adds a lens, not a second source of truth.

/** The options actually set on a task or the workflow, for the badge rows.
 *  The stored dict carries every serializable AgentOptions field, most of them
 *  null on any given definition; showing those would bury the two that matter. */
function setOptions(options: Record<string, unknown>): [string, string][] {
  return Object.entries(options).flatMap(([key, value]): [string, string][] => {
    if (value === null || value === undefined || value === "" || value === false) return [];
    if (Array.isArray(value)) return value.length ? [[key, value.join(" ")]] : [];
    if (typeof value === "object") {
      const entries = Object.entries(value as Record<string, unknown>);
      // Nested configs (mcp_servers) render by their one telling field, not
      // as [object Object].
      const show = (v: unknown) =>
        typeof v === "object" && v !== null && "name" in (v as Record<string, unknown>)
          ? String((v as Record<string, unknown>).name)
          : String(v);
      return entries.length ? [[key, entries.map(([k, v]) => `${k}=${show(v)}`).join(" ")]] : [];
    }
    return [[key, compact(value, 60)]];
  });
}

export default function WorkflowPage() {
  // useSearchParams requires a Suspense boundary under static export
  return (
    <Suspense>
      <WorkflowInner />
    </Suspense>
  );
}

function WorkflowInner() {
  const params = useSearchParams();
  const name = params.get("name");
  // Which run the graph is showing, kept in the URL so one is linkable.
  // Absent = the newest run (or the bare definition before anything has run);
  // "definition" is how you ask for the shape without a run painted on it.
  const runParam = params.get("run");
  const router = useRouter();
  const { workflow, runs, missing, refresh } = useWorkflow(name);
  const now = useNow();
  const [flash, act] = useAction();
  const [editing, setEditing] = useState(false);
  const [focus, setFocus] = useState<string | null>(null);

  if (!name || missing) {
    return (
      <p className="pt-20 text-center text-[13px] text-muted-foreground">
        {name ? `No workflow named ${name}.` : "No workflow selected."}{" "}
        <Link href="/workflows" className="text-primary hover:underline">
          Back to workflows
        </Link>
      </p>
    );
  }

  const graphed =
    runParam === "definition"
      ? null
      : ((runs ?? []).find((run) => run.id === runParam) ?? (runParam ? null : (runs?.[0] ?? null)));
  // An old run is drawn from the task list it captured at launch, not from the
  // definition, which may have been edited since.
  const spec = graphed?.spec ?? workflow?.tasks ?? null;
  const showRun = (id: string | null) =>
    router.replace(`/workflow?name=${encodeURIComponent(name)}&run=${id ?? "definition"}`);

  const paused = workflow ? !workflow.enabled : false;
  const command = (fn: () => Promise<string>) =>
    act(async () => {
      const message = await fn();
      refresh();
      return message;
    });

  return (
    <div className="min-h-0 flex-1 space-y-5 overflow-y-auto p-4 sm:p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <Link
            href="/workflows"
            className="flex items-center gap-1.5 text-[12px] text-muted-foreground hover:text-foreground"
          >
            <ArrowLeft className="size-3.5" />
            Workflows
          </Link>
          <h1 className="pt-1 font-mono text-2xl font-semibold tracking-tight break-all">{name}</h1>
          <WorkflowLine workflow={workflow} now={now} />
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={!workflow}
            onClick={() =>
              command(async () => {
                const { run_id } = await post<{ run_id: string }>(
                  `/api/workflows/${encodeURIComponent(name)}/run`,
                );
                return `started ${run_id}`;
              })
            }
          >
            <Zap />
            Run now
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={!workflow}
            onClick={() => setEditing((on) => !on)}
          >
            <Pencil />
            Edit
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={!workflow || !workflow.cron}
            onClick={() =>
              command(async () => {
                await post(`/api/workflows/${encodeURIComponent(name)}/enabled`, {
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
            disabled={!workflow}
            onClick={() => {
              if (!confirm(`Delete workflow ${name}? Its task sessions are kept.`)) return;
              act(async () => {
                await post(`/api/workflows/${encodeURIComponent(name)}/delete`);
                router.push("/workflows");
                return "deleted";
              });
            }}
          >
            <Trash2 />
            Delete
          </Button>
        </div>
      </div>

      {workflow?.last_error && (
        <p className="rounded-lg border border-destructive/40 bg-card px-3 py-2 text-[12px] text-destructive">
          {workflow.last_error}
        </p>
      )}

      {editing && workflow && (
        <WorkflowForm
          initial={workflow}
          onCancel={() => setEditing(false)}
          onCreated={() => {
            setEditing(false);
            refresh();
          }}
        />
      )}

      <Stats workflow={workflow} runs={runs} />

      <Card>
        <CardHeader>
          <CardTitle>Run history</CardTitle>
          <CardDescription>
            Bar height is wall-clock duration, color is how the run ended
          </CardDescription>
        </CardHeader>
        <CardContent>
          <RunTimeline runs={runs} now={now} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="space-y-1.5">
            <CardTitle>Graph</CardTitle>
            <CardDescription>
              {graphed
                ? "One firing, task by task — click a task to open its session"
                : "The task graph every firing runs — an edge is what waits for what"}
            </CardDescription>
          </div>
          <Select
            aria-label="Graph subject"
            className="sm:max-w-[18rem]"
            value={graphed?.id ?? "definition"}
            onChange={(event) =>
              showRun(event.target.value === "definition" ? null : event.target.value)
            }
          >
            <option value="definition">Definition</option>
            {(runs ?? []).map((run) => (
              <option key={run.id} value={run.id}>
                {shortId(run.id)} · {run.status} · {clockTime(run.started_at)}
              </option>
            ))}
          </Select>
        </CardHeader>
        <CardContent className="space-y-3">
          {workflow === null || spec === null ? (
            <Skeleton className="h-20 w-full" />
          ) : (
            <>
              <WorkflowGraph
                tasks={graphTasks(spec, graphed?.tasks, now)}
                selected={focus}
                onSelect={(id) => {
                  setFocus(id);
                  const session = graphed?.tasks[id]?.session_id;
                  if (session) router.push(`/session?sid=${session}`);
                }}
                empty="This workflow has no tasks."
              />
              {spec.map((task) => (
                <TaskSpecRow
                  key={task.id}
                  task={task}
                  state={graphed?.tasks[task.id]}
                  selected={focus === task.id}
                />
              ))}
              <div className="flex flex-wrap gap-1.5">
                {setOptions(workflow.options).map(([key, value]) => (
                  <Badge
                    key={key}
                    variant="secondary"
                    className="max-w-full font-mono break-words whitespace-normal"
                    title="Workflow-level default, inherited by every task"
                  >
                    {key}: {value}
                  </Badge>
                ))}
                {workflow.created_by && (
                  <Badge className="font-mono">created by {workflow.created_by}</Badge>
                )}
              </div>
            </>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Runs</CardTitle>
          <CardDescription>
            Newest first — every task run is an ordinary session; click one to open its transcript
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-2 pb-4">
          <RunList runs={runs} now={now} graphed={graphed?.id ?? null} onGraph={showRun} />
        </CardContent>
      </Card>
      {flash && <p className="text-center text-[11px] text-muted-foreground">{flash}</p>}
    </div>
  );
}

function WorkflowLine({ workflow, now }: { workflow: WorkflowSummary | null; now: number }) {
  if (!workflow) return <Skeleton className="mt-2 h-4 w-72" />;
  return (
    <p className="flex flex-wrap items-center gap-x-2 gap-y-1 pt-1.5 text-[13px] text-muted-foreground">
      {workflow.cron ? (
        <>
          <code className="font-mono text-foreground">{workflow.cron}</code>
          <span className="text-faint">·</span>
          <span className="font-mono">{workflow.timezone}</span>
          <span className="text-faint">·</span>
          {workflow.enabled ? (
            <span title={clockTime(workflow.next_run_at)}>
              next run {untilTime(workflow.next_run_at, now)}
            </span>
          ) : (
            <Badge>
              <span className="size-[7px] rounded-full bg-faint" />
              paused
            </Badge>
          )}
        </>
      ) : (
        <span>manual-only — fire it with “Run now”</span>
      )}
      <span className="text-faint">·</span>
      <span>
        {workflow.tasks.length} task{workflow.tasks.length === 1 ? "" : "s"}
      </span>
    </p>
  );
}

/** The graph's nodes. Without a run the shape is all there is to show, so the
 *  second line carries the agent or the prompt's opening; with one it carries
 *  the status *word* and how long the task took — color never says it alone. */
function graphTasks(
  spec: WorkflowTaskSpec[],
  state: Record<string, WorkflowTaskState> | undefined,
  now: number,
): GraphTask[] {
  return spec.map((task) => {
    const run = state?.[task.id];
    if (!run) {
      return {
        id: task.id,
        label: task.id,
        depends_on: task.depends_on,
        detail: task.agent ? `as ${task.agent}` : compact(task.prompt.trim().split("\n")[0], 26),
        title: task.prompt,
      };
    }
    const seconds = run.finished_at
      ? (run.finished_at ?? 0) - (run.started_at ?? 0)
      : run.started_at
        ? now - run.started_at
        : null;
    return {
      id: task.id,
      label: task.id,
      depends_on: task.depends_on,
      status: run.status,
      detail: seconds === null ? run.status : `${run.status} · ${duration(seconds)}`,
      title: run.error || run.result_preview || task.prompt,
      disabled: !run.session_id,
    };
  });
}

function TaskSpecRow({
  task,
  state,
  selected,
}: {
  task: WorkflowTaskSpec;
  state?: WorkflowTaskState;
  selected: boolean;
}) {
  // Picking a node above scrolls its prompt into view; "nearest" keeps the
  // page still when the row is already where the eye is.
  const row = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (selected) row.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [selected]);

  return (
    <div
      ref={row}
      className={cn(
        "rounded-lg border border-border bg-surface px-3 py-2",
        selected && "border-transparent ring-2 ring-ring",
      )}
    >
      <div className="flex flex-wrap items-center gap-1.5 pb-1.5">
        <span className="font-mono text-[12px] font-semibold">{task.id}</span>
        {state && <TaskBadge status={state.status} />}
        {task.depends_on.length > 0 && (
          <span className="flex items-center gap-1 font-mono text-[11px] text-muted-foreground">
            <ArrowRight className="size-3" />
            after {task.depends_on.join(", ")}
          </span>
        )}
        {task.agent && (
          <Link href={`/agent?name=${encodeURIComponent(task.agent)}`}>
            <Badge
              className="font-mono"
              title="Stored agent (persona) this task runs as, re-read at each firing"
            >
              agent: {task.agent}
            </Badge>
          </Link>
        )}
        {setOptions(task.options).map(([key, value]) => (
          <Badge
            key={key}
            variant="secondary"
            className="max-w-full font-mono break-words whitespace-normal"
          >
            {key}: {value}
          </Badge>
        ))}
      </div>
      <pre className="chat-prose overflow-x-auto font-mono text-[12px] whitespace-pre-wrap">
        {task.prompt}
      </pre>
    </div>
  );
}

function Stats({
  workflow,
  runs,
}: {
  workflow: WorkflowSummary | null;
  runs: WorkflowRun[] | null;
}) {
  // Rates and averages only mean something over runs that finished; a run
  // still going has no duration and no verdict yet.
  const decided = runs?.filter((r) => r.status !== "running") ?? [];
  const succeeded = decided.filter((r) => r.status === "succeeded").length;
  const finished = decided.filter((r) => r.started_at && r.finished_at);
  const meanDuration = finished.length
    ? finished.reduce((sum, r) => sum + ((r.finished_at ?? 0) - (r.started_at ?? 0)), 0) /
      finished.length
    : null;

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
      <StatCard
        label="Last run"
        value={runs === null ? null : runs.length === 0 ? "never" : runs[0].status}
        sub={runs?.length ? `${runs.length} in history` : "no runs yet"}
      />
      <StatCard
        label="Success rate"
        value={
          runs === null
            ? null
            : decided.length
              ? `${Math.round((succeeded / decided.length) * 100)}%`
              : "—"
        }
        sub={decided.length ? `${succeeded} of ${decided.length} finished runs` : "nothing finished yet"}
      />
      <StatCard
        label="Avg duration"
        value={runs === null ? null : meanDuration === null ? "—" : duration(meanDuration)}
        sub={finished.length ? `over ${finished.length} completed runs` : "no completed runs"}
      />
      <StatCard
        label="Tasks"
        value={workflow === null ? null : String(workflow.tasks.length)}
        sub={
          workflow && workflow.skip_count > 0
            ? `${workflow.skip_count} slot${workflow.skip_count === 1 ? "" : "s"} skipped (run still active)`
            : "per run"
        }
      />
    </div>
  );
}

function RunList({
  runs,
  now,
  graphed,
  onGraph,
}: {
  runs: WorkflowRun[] | null;
  now: number;
  graphed: string | null;
  onGraph: (id: string) => void;
}) {
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
    <>
      {runs.map((run) => {
        const seconds =
          run.finished_at && run.started_at
            ? run.finished_at - run.started_at
            : run.started_at
              ? now - run.started_at
              : null;
        // Definition order, so the chips read the way the tasks were written
        // rather than in dict order. They are a status index, not a sequence —
        // what waits for what is the graph's job, so there are no arrows here.
        const order = run.spec.map((t) => t.id);
        return (
          <div
            key={run.id}
            className={cn(
              "rounded-lg border border-border bg-surface px-3 py-2",
              graphed === run.id && "border-transparent ring-2 ring-ring",
            )}
          >
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 pb-1.5">
              <span className="font-mono text-xs" title={run.id}>
                {shortId(run.id)}
              </span>
              <RunBadge outcome={run.status} />
              <span className="text-xs text-muted-foreground">{run.trigger}</span>
              <span
                className="text-xs text-muted-foreground"
                title={clockTime(run.started_at)}
              >
                {relTime(run.started_at, now)}
              </span>
              <span className="font-mono text-xs tabular-nums text-muted-foreground">
                {duration(seconds)}
                {run.finished_at ? "" : "…"}
              </span>
              <span className="flex-1" />
              <Button
                variant="ghost"
                size="sm"
                className="h-6 px-2 text-[11px]"
                disabled={graphed === run.id}
                onClick={() => onGraph(run.id)}
              >
                <Network />
                Graph
              </Button>
            </div>
            <div className="flex flex-wrap items-center gap-1.5">
              {order.map((taskId) => {
                const state = run.tasks[taskId];
                if (!state) return null;
                return (
                  <span key={taskId} className="flex items-center gap-1.5 pr-1.5">
                    <button
                      type="button"
                      disabled={!state.session_id}
                      title={state.error || state.result_preview || taskId}
                      onClick={() =>
                        state.session_id && router.push(`/session?sid=${state.session_id}`)
                      }
                      className="disabled:cursor-default"
                    >
                      <TaskBadge
                        status={state.status}
                        className={state.session_id ? "cursor-pointer hover:bg-secondary" : ""}
                      />
                    </button>
                    <span className="font-mono text-[11px] text-muted-foreground">{taskId}</span>
                  </span>
                );
              })}
            </div>
            {Object.values(run.tasks).some((t) => t.error) && (
              <p className="pt-1.5 text-[11px] text-destructive">
                {order
                  .filter((tid) => run.tasks[tid]?.error)
                  .map((tid) => `${tid}: ${run.tasks[tid].error}`)
                  .join(" · ")}
              </p>
            )}
          </div>
        );
      })}
    </>
  );
}
