"use client";

import { Suspense, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { ArrowLeft, Pencil, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { AgentForm } from "@/components/agent-form";
import { useAction, useAgent, useNow } from "@/lib/hooks";
import { post } from "@/lib/api";
import { compact, relTime } from "@/lib/format";
import { isDefaultPrompt } from "@/lib/types";
import type { AgentSummary } from "@/lib/types";

// One stored agent: what it is and the options every referencing run inherits.

/** The options actually set on the agent, rendered for the badge row. */
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

/** The agent's system prompt: a persona it was given, the default prompt, or
 *  both — the preset carries its additions in `append`. */
function SystemPrompt({ options }: { options: Record<string, unknown> }) {
  const value = options.system_prompt;
  const preset = isDefaultPrompt(value);
  const text = preset ? (value.append ?? "") : typeof value === "string" ? value : "";
  if (!preset && !text) return null;
  return (
    <div className="space-y-2">
      {preset && (
        <p className="text-[12px] text-muted-foreground">
          Runs with the harness&apos;s default system prompt{text && ", plus:"}
        </p>
      )}
      {text && (
        <pre className="chat-prose overflow-x-auto rounded-lg border border-border bg-surface px-3 py-2 font-mono text-[12px] whitespace-pre-wrap">
          {text}
        </pre>
      )}
    </div>
  );
}

export default function AgentPage() {
  // useSearchParams requires a Suspense boundary under static export
  return (
    <Suspense>
      <AgentInner />
    </Suspense>
  );
}

function AgentInner() {
  const name = useSearchParams().get("name");
  const router = useRouter();
  const { agent, missing, refresh } = useAgent(name);
  const now = useNow();
  const [flash, act] = useAction();
  const [editing, setEditing] = useState(false);

  if (!name || missing) {
    return (
      <p className="pt-20 text-center text-[13px] text-muted-foreground">
        {name ? `No agent named ${name}.` : "No agent selected."}{" "}
        <Link href="/agents" className="text-primary hover:underline">
          Back to agents
        </Link>
      </p>
    );
  }

  return (
    <div className="min-h-0 flex-1 space-y-5 overflow-y-auto p-4 sm:p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <Link
            href="/agents"
            className="flex items-center gap-1.5 text-[12px] text-muted-foreground hover:text-foreground"
          >
            <ArrowLeft className="size-3.5" />
            Agents
          </Link>
          <h1 className="pt-1 font-mono text-2xl font-semibold tracking-tight break-all">{name}</h1>
          <AgentLine agent={agent} now={now} />
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={!agent || editing}
            onClick={() => setEditing(true)}
          >
            <Pencil />
            Edit
          </Button>
          <Button
            variant="destructive"
            size="sm"
            disabled={!agent}
            onClick={() => {
              if (!confirm(`Delete agent ${name}? Sessions that ran as it are kept.`)) return;
              act(async () => {
                await post(`/api/agents/${encodeURIComponent(name)}/delete`);
                router.push("/agents");
                return "deleted";
              });
            }}
          >
            <Trash2 />
            Delete
          </Button>
        </div>
      </div>

      {editing && agent ? (
        <AgentForm
          agent={agent}
          onCancel={() => setEditing(false)}
          onSaved={() => {
            setEditing(false);
            refresh();
          }}
        />
      ) : (
        <Card>
          <CardHeader>
            <CardTitle>Configuration</CardTitle>
            <CardDescription>
              The defaults every session or workflow task referencing this agent inherits — explicitly
              set options on the caller's side override per field
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {agent === null ? (
              <Skeleton className="h-20 w-full" />
            ) : (
              <>
                <SystemPrompt options={agent.options} />
                <div className="flex flex-wrap gap-1.5">
                  {setOptions(agent.options)
                    .filter(([key]) => key !== "system_prompt")
                    .map(([key, value]) => (
                      <Badge
                        key={key}
                        variant="secondary"
                        className="max-w-full font-mono break-words whitespace-normal"
                      >
                        {key}: {value}
                      </Badge>
                    ))}
                  {agent.created_by && (
                    <Badge className="font-mono">created by {agent.created_by}</Badge>
                  )}
                </div>
              </>
            )}
          </CardContent>
        </Card>
      )}
      {flash && <p className="text-center text-[11px] text-muted-foreground">{flash}</p>}
    </div>
  );
}

function AgentLine({ agent, now }: { agent: AgentSummary | null; now: number }) {
  if (!agent) return <Skeleton className="mt-2 h-4 w-72" />;
  return (
    <p className="flex flex-wrap items-center gap-x-2 gap-y-1 pt-1.5 text-[13px] text-muted-foreground">
      {agent.description && <span>{agent.description}</span>}
      {agent.description && <span className="text-faint">·</span>}
      <span>updated {relTime(agent.updated_at, now)}</span>
    </p>
  );
}
