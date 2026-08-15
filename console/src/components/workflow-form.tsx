"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  BigQueryToggle,
  buildOptionsPayload,
  ChoiceField,
  ClaudeCodeToggle,
  ConnectorPicker,
  Field,
  MODELS,
  ToolPicker,
  useOptionsDraft,
} from "@/components/option-fields";
import { useAgents, useArtifactSpaces, useWorkspaces } from "@/lib/hooks";
import { post } from "@/lib/api";
import { cn } from "@/lib/utils";

// The presets cover what people actually schedule; anything else is typed in.
const PRESETS: { label: string; cron: string }[] = [
  { label: "Manual only", cron: "" },
  { label: "Hourly", cron: "0 * * * *" },
  { label: "Daily 9am", cron: "0 9 * * *" },
  { label: "Weekdays 9am", cron: "0 9 * * MON-FRI" },
  { label: "Monday 8am", cron: "0 8 * * MON" },
  { label: "Every 15m", cron: "*/15 * * * *" },
];

const TASKS_PLACEHOLDER = `[
  { "id": "research", "prompt": "find the numbers" },
  { "id": "report", "prompt": "write it up from: {{tasks.research.result}}" }
]`;

function browserZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

/** New-workflow form. The simple mode is one prompt (a one-task workflow, the
 *  old "deployment"); the chain mode takes the tasks array as JSON. Options
 *  mirror the AgentOptions subset a session stores and become the workflow's
 *  defaults, posted as that same serialized dict — the server rejects anything
 *  it doesn't recognize rather than dropping it. */
export function WorkflowForm({
  onCreated,
  onCancel,
}: {
  onCreated: (name: string) => void;
  onCancel: () => void;
}) {
  const workspaces = useWorkspaces();
  const spaces = useArtifactSpaces();
  const { agents } = useAgents();
  const draft = useOptionsDraft();
  const [name, setName] = useState("");
  const [agent, setAgent] = useState("");
  const [cron, setCron] = useState("0 9 * * *");
  const [timezone, setTimezone] = useState(browserZone());
  const [chain, setChain] = useState(false);
  const [prompt, setPrompt] = useState("");
  const [tasksJson, setTasksJson] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      let tasks: unknown = null;
      if (chain) {
        tasks = JSON.parse(tasksJson); // surfaced as a form error if invalid
        if (!Array.isArray(tasks)) throw new Error("tasks must be a JSON array");
      }
      await post("/api/workflows", {
        name: name.trim(),
        cron: cron.trim() || null,
        timezone: timezone.trim(),
        ...(chain ? { tasks } : { prompt, agent: agent.trim() || null }),
        options: buildOptionsPayload(draft),
      });
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
        <CardTitle>New workflow</CardTitle>
        <CardDescription>
          A named chain of one-shot tasks — every firing runs each task as a fresh session. One
          task and a cron is the classic scheduled prompt.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={submit} className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-3">
            <Field label="Name" hint="lowercase, no spaces">
              <Input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="nightly-report"
                autoFocus
                required
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
            {PRESETS.map((preset) => (
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
          <div className="flex gap-1.5">
            {[false, true].map((mode) => (
              <button
                key={String(mode)}
                type="button"
                onClick={() => setChain(mode)}
                className={cn(
                  "rounded-full border px-2.5 py-0.5 text-[11px] transition-colors",
                  chain === mode
                    ? "border-transparent bg-primary-soft text-foreground"
                    : "border-border text-muted-foreground hover:bg-secondary",
                )}
              >
                {mode ? "Task chain (JSON)" : "Single task"}
              </button>
            ))}
          </div>
          {chain ? (
            <Field
              label="Tasks"
              hint="id, prompt, agent?, options?, depends_on? — omitted depends_on chains to the previous task"
            >
              <Textarea
                value={tasksJson}
                onChange={(e) => setTasksJson(e.target.value)}
                rows={8}
                required
                placeholder={TASKS_PLACEHOLDER}
                className="rounded-lg border border-input bg-card px-3 py-2 font-mono text-[12px]"
              />
            </Field>
          ) : (
            <Field label="Prompt">
              <Textarea
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                rows={3}
                required
                placeholder="Profile the CSVs in the workspace and write report.md"
                className="rounded-lg border border-input bg-card px-3 py-2 text-[13px]"
              />
            </Field>
          )}
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
            {!chain && (
              <Field label="Agent" hint="stored persona">
                <ChoiceField
                  value={agent}
                  onChange={setAgent}
                  choices={(agents ?? []).map((a) => a.name)}
                  noneLabel="none"
                />
              </Field>
            )}
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
          <Field label="System prompt" hint="run tasks as Claude Code itself, with no stored persona">
            <ClaudeCodeToggle on={draft.claudeCode} onChange={draft.setClaudeCode} />
          </Field>
          <Field label="Allowed tools" hint="click to toggle; defaults for every task">
            <ToolPicker draft={draft} />
          </Field>
          <Field label="Connectors" hint="official hosted MCP servers; ∅ = no credential yet">
            <ConnectorPicker value={draft.connectors} onChange={draft.setConnectors} />
          </Field>
          <Field label="BigQuery" hint="read-only SQL; pre-allows its tool">
            <BigQueryToggle on={draft.bigquery} onChange={draft.setBigquery} />
          </Field>
          {error && <p className="text-[12px] text-destructive">{error}</p>}
          <div className="flex items-center gap-2">
            <Button type="submit" size="sm" disabled={busy}>
              {busy ? "Creating…" : "Create workflow"}
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
