"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Pause, Play, Plus, Trash2, Zap } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
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
import { WorkflowForm } from "@/components/workflow-form";
import { useAction, useNow, useWorkflows } from "@/lib/hooks";
import { post } from "@/lib/api";
import { clockTime, relTime, untilTime } from "@/lib/format";
import type { WorkflowSummary } from "@/lib/types";

export default function WorkflowsPage() {
  const { workflows, refresh } = useWorkflows();
  const now = useNow();
  const router = useRouter();
  const [flash, run] = useAction();
  const [creating, setCreating] = useState(false);

  const act = (fn: () => Promise<string>) =>
    run(async () => {
      const message = await fn();
      refresh();
      return message;
    });

  return (
    <div className="min-h-0 flex-1 space-y-5 overflow-y-auto p-4 sm:p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="font-serif text-2xl tracking-tight">Workflows</h1>
        {!creating && (
          <Button size="sm" onClick={() => setCreating(true)}>
            <Plus />
            New workflow
          </Button>
        )}
      </div>
      {creating && (
        <WorkflowForm
          onCancel={() => setCreating(false)}
          onCreated={(name) => {
            setCreating(false);
            router.push(`/workflow?name=${encodeURIComponent(name)}`);
          }}
        />
      )}
      <Card>
        <CardContent className="px-2 py-2">
          {workflows === null ? (
            <div className="space-y-2 p-2">
              <Skeleton className="h-8" />
              <Skeleton className="h-8" />
            </div>
          ) : workflows.length === 0 ? (
            <p className="p-10 text-center text-[13px] text-muted-foreground">
              No workflows yet — a workflow chains one-shot tasks, each run as a fresh session,
              on a cron or on demand.
              <br />
              Create one above, or with{" "}
              <code className="font-mono text-xs">
                syros workflows create nightly --cron &quot;0 9 * * *&quot; --prompt &quot;…&quot;
              </code>
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead className="text-right">Tasks</TableHead>
                  <TableHead>Cron</TableHead>
                  <TableHead>Next run</TableHead>
                  <TableHead>Last run</TableHead>
                  <TableHead className="text-right">Runs</TableHead>
                  <TableHead className="w-28" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {workflows.map((workflow) => (
                  <WorkflowRow key={workflow.name} workflow={workflow} now={now} act={act} />
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
      {flash && <p className="text-center text-[11px] text-muted-foreground">{flash}</p>}
    </div>
  );
}

function WorkflowRow({
  workflow,
  now,
  act,
}: {
  workflow: WorkflowSummary;
  now: number;
  act: (fn: () => Promise<string>) => void;
}) {
  const router = useRouter();
  const href = `/workflow?name=${encodeURIComponent(workflow.name)}`;
  const paused = !workflow.enabled;
  const manual = !workflow.cron;

  return (
    <TableRow className="cursor-pointer" onClick={() => router.push(href)}>
      <TableCell className="font-mono text-[13px] font-medium">
        <Link href={href} onClick={(e) => e.stopPropagation()} className="hover:underline">
          {workflow.name}
        </Link>
        {workflow.last_error && (
          <div className="max-w-[22rem] truncate text-[11px] text-destructive">
            {workflow.last_error}
          </div>
        )}
      </TableCell>
      <TableCell className="text-right font-mono text-xs tabular-nums">
        {workflow.tasks.length}
      </TableCell>
      <TableCell className="font-mono text-xs text-muted-foreground">
        {manual ? (
          <span className="text-faint">manual</span>
        ) : (
          <>
            {workflow.cron}
            <span className="pl-2 text-faint">{workflow.timezone}</span>
          </>
        )}
      </TableCell>
      <TableCell className="text-xs">
        {manual ? (
          <span className="text-faint">—</span>
        ) : paused ? (
          <Badge>
            <span className="size-[7px] rounded-full bg-faint" />
            paused
          </Badge>
        ) : (
          <span title={clockTime(workflow.next_run_at)}>
            {untilTime(workflow.next_run_at, now)}
          </span>
        )}
      </TableCell>
      <TableCell>
        {workflow.last_run ? (
          <span className="flex items-center gap-2">
            <RunBadge outcome={workflow.last_run.status} />
            <span className="text-[11px] text-muted-foreground">
              {relTime(workflow.last_run_at, now)}
            </span>
          </span>
        ) : (
          <span className="text-[11px] text-faint">never run</span>
        )}
      </TableCell>
      <TableCell className="text-right font-mono text-xs tabular-nums">
        {workflow.run_count}
        {workflow.skip_count > 0 && (
          <span
            className="pl-2 text-[11px] text-faint"
            title="Slots that fired while the previous run was still active"
          >
            {workflow.skip_count} skipped
          </span>
        )}
      </TableCell>
      <TableCell className="w-28 text-right" onClick={(e) => e.stopPropagation()}>
        <Button
          variant="ghost"
          size="icon"
          className="size-7"
          title="Run now"
          onClick={() =>
            act(async () => {
              const { run_id } = await post<{ run_id: string }>(
                `/api/workflows/${encodeURIComponent(workflow.name)}/run`,
              );
              return `started ${run_id}`;
            })
          }
        >
          <Zap />
        </Button>
        <Button
          variant="ghost"
          size="icon"
          className="size-7"
          title={paused ? "Resume" : "Pause"}
          onClick={() =>
            act(async () => {
              await post(`/api/workflows/${encodeURIComponent(workflow.name)}/enabled`, {
                enabled: paused,
              });
              return paused ? `resumed ${workflow.name}` : `paused ${workflow.name}`;
            })
          }
        >
          {paused ? <Play /> : <Pause />}
        </Button>
        <Button
          variant="ghost"
          size="icon"
          className="size-7 hover:text-destructive"
          title="Delete workflow"
          onClick={() => {
            if (!confirm(`Delete workflow ${workflow.name}? Its task sessions are kept.`)) return;
            act(async () => {
              await post(`/api/workflows/${encodeURIComponent(workflow.name)}/delete`);
              return `deleted ${workflow.name}`;
            });
          }}
        >
          <Trash2 />
        </Button>
      </TableCell>
    </TableRow>
  );
}
