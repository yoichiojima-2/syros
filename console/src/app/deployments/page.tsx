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
import { DeploymentForm } from "@/components/deployment-form";
import { useAction, useNow, useDeployments } from "@/lib/hooks";
import { post } from "@/lib/api";
import { clockTime, relTime, untilTime } from "@/lib/format";
import type { DeploymentSummary } from "@/lib/types";

export default function DeploymentsPage() {
  const { deployments, refresh } = useDeployments();
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
    <div className="min-h-0 flex-1 space-y-5 overflow-y-auto p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="font-serif text-2xl tracking-tight">Deployments</h1>
        {!creating && (
          <Button size="sm" onClick={() => setCreating(true)}>
            <Plus />
            New deployment
          </Button>
        )}
      </div>
      {creating && (
        <DeploymentForm
          onCancel={() => setCreating(false)}
          onCreated={(name) => {
            setCreating(false);
            router.push(`/deployment?name=${encodeURIComponent(name)}`);
          }}
        />
      )}
      <Card>
        <CardContent className="px-2 py-2">
          {deployments === null ? (
            <div className="space-y-2 p-2">
              <Skeleton className="h-8" />
              <Skeleton className="h-8" />
            </div>
          ) : deployments.length === 0 ? (
            <p className="p-10 text-center text-[13px] text-muted-foreground">
              No deployments yet — a deployment fires a fresh session on a cron.
              <br />
              Create one above, or with{" "}
              <code className="font-mono text-xs">
                syros deployments create nightly --cron &quot;0 9 * * *&quot; --prompt &quot;…&quot;
              </code>
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Cron</TableHead>
                  <TableHead>Next run</TableHead>
                  <TableHead>Last run</TableHead>
                  <TableHead className="text-right">Runs</TableHead>
                  <TableHead className="w-28" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {deployments.map((deployment) => (
                  <DeploymentRow key={deployment.name} deployment={deployment} now={now} act={act} />
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

function DeploymentRow({
  deployment,
  now,
  act,
}: {
  deployment: DeploymentSummary;
  now: number;
  act: (fn: () => Promise<string>) => void;
}) {
  const router = useRouter();
  const href = `/deployment?name=${encodeURIComponent(deployment.name)}`;
  const paused = !deployment.enabled;

  return (
    <TableRow className="cursor-pointer" onClick={() => router.push(href)}>
      <TableCell className="font-mono text-[13px] font-medium">
        <Link href={href} onClick={(e) => e.stopPropagation()} className="hover:underline">
          {deployment.name}
        </Link>
        {deployment.last_error && (
          <div className="max-w-[22rem] truncate text-[11px] text-destructive">
            {deployment.last_error}
          </div>
        )}
      </TableCell>
      <TableCell className="font-mono text-xs text-muted-foreground">
        {deployment.cron}
        <span className="pl-2 text-faint">{deployment.timezone}</span>
      </TableCell>
      <TableCell className="text-xs">
        {paused ? (
          <Badge>
            <span className="size-[7px] rounded-full bg-faint" />
            paused
          </Badge>
        ) : (
          <span title={clockTime(deployment.next_run_at)}>
            {untilTime(deployment.next_run_at, now)}
          </span>
        )}
      </TableCell>
      <TableCell>
        {deployment.last_run ? (
          <span className="flex items-center gap-2">
            <RunBadge outcome={deployment.last_run.outcome} />
            <span className="text-[11px] text-muted-foreground">
              {relTime(deployment.last_run_at, now)}
            </span>
          </span>
        ) : (
          <span className="text-[11px] text-faint">never run</span>
        )}
      </TableCell>
      <TableCell className="text-right font-mono text-xs tabular-nums">
        {deployment.runs}
        {deployment.skips > 0 && (
          <span
            className="pl-2 text-[11px] text-faint"
            title="Slots that fired while the previous run was still active"
          >
            {deployment.skips} skipped
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
              const { session_id } = await post<{ session_id: string }>(
                `/api/deployments/${encodeURIComponent(deployment.name)}/run`,
              );
              return `started ${session_id}`;
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
              await post(`/api/deployments/${encodeURIComponent(deployment.name)}/enabled`, {
                enabled: paused,
              });
              return paused ? `resumed ${deployment.name}` : `paused ${deployment.name}`;
            })
          }
        >
          {paused ? <Play /> : <Pause />}
        </Button>
        <Button
          variant="ghost"
          size="icon"
          className="size-7 hover:text-destructive"
          title="Delete deployment"
          onClick={() => {
            if (!confirm(`Delete deployment ${deployment.name}? Its past runs are kept.`)) return;
            act(async () => {
              await post(`/api/deployments/${encodeURIComponent(deployment.name)}/delete`);
              return `deleted ${deployment.name}`;
            });
          }}
        >
          <Trash2 />
        </Button>
      </TableCell>
    </TableRow>
  );
}
