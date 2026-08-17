"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  BigQueryToggle,
  buildOptionsPayload,
  ChoiceField,
  ConnectorPicker,
  Field,
  MODELS,
  ToolPicker,
  useOptionsDraft,
} from "@/components/option-fields";
import { emptyTask, TaskListEditor, type TaskDraft } from "@/components/task-list-editor";
import { useAgents, useArtifactSpaces, useWorkspaces } from "@/lib/hooks";
import { post } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { WorkflowSummary } from "@/lib/types";

// Cron shortcuts, not to be confused with the object presets in /api/presets:
// these cover what people actually schedule; anything else is typed in.
const CRON_PRESETS: { label: string; cron: string }[] = [
  { label: "Manual only", cron: "" },
  { label: "Hourly", cron: "0 * * * *" },
  { label: "Daily 9am", cron: "0 9 * * *" },
  { label: "Weekdays 9am", cron: "0 9 * * MON-FRI" },
  { label: "Monday 8am", cron: "0 8 * * MON" },
  { label: "Every 15m", cron: "*/15 * * * *" },
];

function browserZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

/** The stored tasks of a workflow being edited, as editor drafts. Stored
 *  depends_on is always explicit (normalize_tasks resolves the linear default
 *  at write time), and the editor holds dependencies by draft key, so the ids
 *  are mapped over on the way in and back out at submit. */
function draftsFrom(workflow: WorkflowSummary): TaskDraft[] {
  const keyOf = new Map(workflow.tasks.map((task, i) => [task.id, `stored-${i}`]));
  return workflow.tasks.map((task, i) => ({
    key: `stored-${i}`,
    id: task.id,
    prompt: task.prompt,
    agent: task.agent ?? "",
    dependsOn: (task.depends_on ?? []).map((id) => keyOf.get(id) ?? id),
    options: task.options,
  }));
}

/** Option keys `useOptionsDraft` reads and `buildOptionsPayload` writes. An
 *  edit full-replaces the stored dict, so anything outside this set — tools,
 *  disallowed_tools, MCP servers other than the BigQuery toggle's — has to be
 *  carried across or the console would quietly drop options only the CLI can
 *  set. */
const DRAFT_FIELDS = new Set([
  "system_prompt",
  "model",
  "permission_mode",
  "workspace",
  "artifacts",
  "allowed_tools",
  "connectors",
  "max_budget_usd",
  "max_turns",
]);

function optionsPayload(
  draft: Parameters<typeof buildOptionsPayload>[0],
  stored: Record<string, unknown> | undefined,
): Record<string, unknown> {
  const payload = buildOptionsPayload(draft);
  if (!stored) return payload;
  const carried = Object.fromEntries(
    Object.entries(stored).filter(
      ([key, value]) =>
        !DRAFT_FIELDS.has(key) &&
        key !== "mcp_servers" &&
        value !== null &&
        !(Array.isArray(value) && value.length === 0),
    ),
  );
  // mcp_servers is half-modelled: the BigQuery pill owns the `bq` entry and
  // knows nothing about the rest.
  const servers = (stored.mcp_servers ?? {}) as Record<string, unknown>;
  const others = Object.fromEntries(Object.entries(servers).filter(([key]) => key !== "bq"));
  const bq = (payload.mcp_servers as Record<string, unknown> | undefined)?.bq;
  const merged = { ...others, ...(bq ? { bq } : {}) };
  return {
    ...carried,
    ...payload,
    ...(Object.keys(merged).length ? { mcp_servers: merged } : {}),
  };
}

/** Workflow definition form, for a new workflow or (with `initial`) an edit of
 *  an existing one. The task list is always the shape being edited — a
 *  one-task workflow is just the short list, and it's the classic scheduled
 *  prompt. Options mirror the AgentOptions subset a session stores and become
 *  the workflow's defaults, posted as that same serialized dict — the server
 *  rejects anything it doesn't recognize rather than dropping it. */
export function WorkflowForm({
  onCreated,
  onCancel,
  initial,
}: {
  onCreated: (name: string) => void;
  onCancel: () => void;
  initial?: WorkflowSummary;
}) {
  const workspaces = useWorkspaces();
  const spaces = useArtifactSpaces();
  const { agents } = useAgents();
  const draft = useOptionsDraft(initial?.options ?? {});
  const editing = initial !== undefined;
  const [name, setName] = useState(initial?.name ?? "");
  const [cron, setCron] = useState(initial ? (initial.cron ?? "") : "0 9 * * *");
  const [timezone, setTimezone] = useState(initial?.timezone || browserZone());
  const [tasks, setTasks] = useState<TaskDraft[]>(
    initial ? draftsFrom(initial) : [emptyTask("main")],
  );
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      // Dependencies are held by draft key while editing; the wire wants ids.
      const idOf = new Map(tasks.map((task) => [task.key, task.id.trim()]));
      const payload = tasks.map((task) => ({
        id: task.id.trim(),
        prompt: task.prompt,
        agent: task.agent.trim() || null,
        // An untouched dependency control stays off the payload so the server
        // applies its linear default.
        ...(task.dependsOn !== null
          ? { depends_on: task.dependsOn.map((key) => idOf.get(key) ?? key) }
          : {}),
        ...(task.options ? { options: task.options } : {}),
      }));
      const body = {
        cron: cron.trim() || null,
        timezone: timezone.trim(),
        tasks: payload,
        options: optionsPayload(draft, initial?.options),
      };
      await post(
        editing ? `/api/workflows/${encodeURIComponent(name)}/update` : "/api/workflows",
        editing ? body : { name: name.trim(), ...body },
      );
      onCreated(name.trim());
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>{editing ? `Edit ${initial.name}` : "New workflow"}</CardTitle>
        <CardDescription>
          {editing
            ? "Edits apply to future runs — a run in flight keeps the definition it started with."
            : "Tasks run one after another, each as a fresh session. One task and a cron is the classic scheduled prompt."}
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={submit} className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-3">
            <Field label="Name" hint={editing ? "fixed" : "lowercase, no spaces"}>
              <Input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="nightly-report"
                autoFocus={!editing}
                readOnly={editing}
                required
                className={cn("font-mono", editing && "text-muted-foreground")}
              />
            </Field>
            <Field label="Cron" hint="empty = manual-only">
              <Input
                value={cron}
                onChange={(e) => setCron(e.target.value)}
                className="font-mono"
                placeholder="manual only"
              />
            </Field>
            <Field label="Timezone" hint="IANA name">
              <Input
                value={timezone}
                onChange={(e) => setTimezone(e.target.value)}
                className="font-mono"
                required
              />
            </Field>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {CRON_PRESETS.map((preset) => (
              <button
                key={preset.label}
                type="button"
                onClick={() => setCron(preset.cron)}
                className={cn(
                  "rounded-full border px-2.5 py-0.5 text-[11px] transition-colors",
                  cron === preset.cron
                    ? "border-transparent bg-primary-soft text-foreground"
                    : "border-border text-muted-foreground hover:bg-secondary",
                )}
              >
                {preset.label}
              </button>
            ))}
          </div>
          {/* A plain heading, not Field: Field is a <label>, and the task list
              carries its own labels and buttons. */}
          <div className="space-y-1.5">
            <span className="flex items-baseline gap-1.5">
              <span className="text-[11px] font-medium tracking-[0.08em] text-muted-foreground uppercase">
                Tasks
              </span>
              <span className="text-[10px] text-faint">run in order; each one a fresh session</span>
            </span>
            <TaskListEditor
              tasks={tasks}
              onChange={setTasks}
              agents={(agents ?? []).map((a) => a.name)}
            />
          </div>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Field label="Model">
              <ChoiceField
                value={draft.model}
                onChange={draft.setModel}
                choices={MODELS}
                noneLabel="default"
              />
            </Field>
            <Field label="Workspace">
              <ChoiceField
                value={draft.workspace}
                onChange={draft.setWorkspace}
                choices={(workspaces ?? []).map((t) => t.name)}
                noneLabel="none"
                customLabel="new workspace…"
              />
            </Field>
            <Field label="Artifact space">
              <ChoiceField
                value={draft.artifacts}
                onChange={draft.setArtifacts}
                choices={(spaces ?? []).map((s) => s.name)}
                noneLabel="none"
                customLabel="new space…"
              />
            </Field>
            <Field label="Budget (USD)">
              <Input
                value={draft.budget}
                onChange={(e) => draft.setBudget(e.target.value)}
                inputMode="decimal"
                placeholder="none"
                className="font-mono"
              />
            </Field>
          </div>
          <Field label="Allowed tools" hint="click to toggle; applies to every task">
            <ToolPicker draft={draft} />
          </Field>
          <Field label="Connectors" hint="official hosted MCP servers; ∅ = no credential yet">
            <ConnectorPicker value={draft.connectors} onChange={draft.setConnectors} />
          </Field>
          <Field label="BigQuery" hint="read-only SQL, plus the agents' own dataset">
            <BigQueryToggle
              on={draft.bigquery}
              onChange={draft.setBigquery}
              write={draft.bigqueryWrite}
              onWriteChange={draft.setBigqueryWrite}
            />
          </Field>
          {error && <p className="text-[12px] text-destructive">{error}</p>}
          <div className="flex items-center gap-2">
            <Button type="submit" size="sm" disabled={busy}>
              {busy
                ? editing
                  ? "Saving…"
                  : "Creating…"
                : editing
                  ? "Save workflow"
                  : "Create workflow"}
            </Button>
            <Button type="button" variant="ghost" size="sm" onClick={onCancel}>
              Cancel
            </Button>
            <span className="text-[11px] text-muted-foreground">
              Unlisted tools still pause for approval.
            </span>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}
