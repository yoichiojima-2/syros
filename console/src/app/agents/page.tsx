"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { MessageSquarePlus, Plus, Trash2 } from "lucide-react";
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
import { AgentForm } from "@/components/agent-form";
import { InstallPresetsButton } from "@/components/install-presets-button";
import { useAction, useAgents, useNow } from "@/lib/hooks";
import { post } from "@/lib/api";
import { relTime } from "@/lib/format";
import type { AgentSummary } from "@/lib/types";

export default function AgentsPage() {
  const { agents, refresh } = useAgents();
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
        <h1 className="font-serif text-2xl tracking-tight">Agents</h1>
        {!creating && (
          <Button size="sm" onClick={() => setCreating(true)}>
            <Plus />
            New agent
          </Button>
        )}
      </div>
      {creating && (
        <AgentForm
          onCancel={() => setCreating(false)}
          onSaved={(name) => {
            setCreating(false);
            router.push(`/agent?name=${encodeURIComponent(name)}`);
          }}
        />
      )}
      <Card>
        <CardContent className="px-2 py-2">
          {agents === null ? (
            <div className="space-y-2 p-2">
              <Skeleton className="h-8" />
              <Skeleton className="h-8" />
            </div>
          ) : agents.length === 0 ? (
            <div className="space-y-4 p-10 text-center text-[13px] text-muted-foreground">
              <p>
                No agents yet — an agent is a stored run configuration (persona) that sessions and
                workflow tasks reference by name.
                <br />
                Create one above, or with{" "}
                <code className="font-mono text-xs">
                  syros agents create reviewer --system-prompt &quot;…&quot; --allow Read
                </code>
              </p>
              <div>
                <InstallPresetsButton />
                <p className="mt-2 text-[11px]">
                  Four example agents, a workspace, two workflows and two skills — editable, and
                  scheduled ones install paused.
                </p>
              </div>
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Description</TableHead>
                  <TableHead>Model</TableHead>
                  <TableHead>Tools</TableHead>
                  <TableHead>Updated</TableHead>
                  <TableHead className="w-20" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {agents.map((agent) => (
                  <AgentRow key={agent.name} agent={agent} now={now} act={act} />
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

function AgentRow({
  agent,
  now,
  act,
}: {
  agent: AgentSummary;
  now: number;
  act: (fn: () => Promise<string>) => void;
}) {
  const router = useRouter();
  const href = `/agent?name=${encodeURIComponent(agent.name)}`;
  const tools = (agent.options.allowed_tools as string[] | null) ?? [];

  return (
    <TableRow className="cursor-pointer" onClick={() => router.push(href)}>
      <TableCell className="font-mono text-[13px] font-medium">
        <Link href={href} onClick={(e) => e.stopPropagation()} className="hover:underline">
          {agent.name}
        </Link>
      </TableCell>
      <TableCell className="max-w-[22rem] truncate text-xs text-muted-foreground">
        {agent.description || "—"}
      </TableCell>
      <TableCell className="font-mono text-xs text-muted-foreground">
        {(agent.options.model as string) || "default"}
      </TableCell>
      <TableCell className="max-w-[18rem] truncate font-mono text-[11px] text-muted-foreground">
        {tools.length ? tools.join(" ") : "—"}
      </TableCell>
      <TableCell className="text-xs text-muted-foreground">
        {relTime(agent.updated_at, now)}
      </TableCell>
      <TableCell className="w-20 text-right" onClick={(e) => e.stopPropagation()}>
        {/* A real link, so the launcher opens in a tab on cmd-click; the agent
            page leads with the prompt box. */}
        <Button variant="ghost" size="icon" className="size-7 hover:text-primary" asChild>
          <Link href={href} title={`New session as ${agent.name}`}>
            <MessageSquarePlus />
          </Link>
        </Button>
        <Button
          variant="ghost"
          size="icon"
          className="size-7 hover:text-destructive"
          title="Delete agent"
          onClick={() => {
            if (!confirm(`Delete agent ${agent.name}? Sessions that ran as it are kept.`)) return;
            act(async () => {
              await post(`/api/agents/${encodeURIComponent(agent.name)}/delete`);
              return `deleted ${agent.name}`;
            });
          }}
        >
          <Trash2 />
        </Button>
      </TableCell>
    </TableRow>
  );
}
