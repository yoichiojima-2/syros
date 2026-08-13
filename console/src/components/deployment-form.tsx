"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { useArtifactSpaces, useWorkspaces } from "@/lib/hooks";
import { post } from "@/lib/api";
import { cn } from "@/lib/utils";

// The presets cover what people actually deployment; anything else is typed in.
const PRESETS: { label: string; cron: string }[] = [
  { label: "Hourly", cron: "0 * * * *" },
  { label: "Daily 9am", cron: "0 9 * * *" },
  { label: "Weekdays 9am", cron: "0 9 * * MON-FRI" },
  { label: "Monday 8am", cron: "0 8 * * MON" },
  { label: "Every 15m", cron: "*/15 * * * *" },
];

// The models people actually pick from; anything else goes through "custom".
const MODELS = ["claude-sonnet-5", "claude-opus-5", "claude-haiku-4-5"];

// The tools people actually allowlist; anything else is typed into "more".
const TOOLS = ["Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebFetch", "WebSearch", "Task"];

function browserZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

/** New-deployment form. Run options mirror the AgentOptions subset a session
 *  stores, and are posted as that same serialized dict — the server rejects
 *  anything it doesn't recognize rather than dropping it. */
export function DeploymentForm({
  onCreated,
  onCancel,
}: {
  onCreated: (name: string) => void;
  onCancel: () => void;
}) {
  const workspaces = useWorkspaces();
  const spaces = useArtifactSpaces();
  const [name, setName] = useState("");
  const [cron, setCron] = useState("0 9 * * *");
  const [timezone, setTimezone] = useState(browserZone());
  const [prompt, setPrompt] = useState("");
  const [model, setModel] = useState("");
  const [workspace, setWorkspace] = useState("");
  const [artifacts, setArtifacts] = useState("");
  const [tools, setTools] = useState<string[]>([]);
  const [extraTools, setExtraTools] = useState("");
  const [budget, setBudget] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    // Only send what was filled in: an empty string is "unset", not "".
    const options: Record<string, unknown> = {};
    if (model.trim()) options.model = model.trim();
    if (workspace.trim()) options.workspace = workspace.trim();
    if (artifacts.trim()) options.artifacts = artifacts.trim();
    const allowed = [
      ...tools,
      ...extraTools
        .split(",")
        .map((tool) => tool.trim())
        .filter((tool) => tool && !tools.includes(tool)),
    ];
    if (allowed.length) options.allowed_tools = allowed;
    if (budget.trim()) options.max_budget_usd = Number(budget);
    try {
      await post("/api/deployments", {
        name: name.trim(),
        cron: cron.trim(),
        timezone: timezone.trim(),
        prompt,
        options,
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
        <CardTitle>New deployment</CardTitle>
        <CardDescription>
          Every firing starts a fresh session with these options and this prompt.
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
            <Field label="Cron" hint="minute hour day month weekday">
              <Input
                value={cron}
                onChange={(e) => setCron(e.target.value)}
                className="font-mono"
                required
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
                key={preset.cron}
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
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Field label="Model">
              <ChoiceField
                value={model}
                onChange={setModel}
                choices={MODELS}
                noneLabel="default"
              />
            </Field>
            <Field label="Workspace">
              <ChoiceField
                value={workspace}
                onChange={setWorkspace}
                choices={(workspaces ?? []).map((w) => w.name)}
                noneLabel="none"
                customLabel="new workspace…"
              />
            </Field>
            <Field label="Artifact space">
              <ChoiceField
                value={artifacts}
                onChange={setArtifacts}
                choices={(spaces ?? []).map((s) => s.name)}
                noneLabel="none"
                customLabel="new space…"
              />
            </Field>
            <Field label="Budget (USD)">
              <Input
                value={budget}
                onChange={(e) => setBudget(e.target.value)}
                inputMode="decimal"
                placeholder="none"
                className="font-mono"
              />
            </Field>
          </div>
          <Field label="Allowed tools" hint="click to toggle">
            <div className="flex flex-wrap items-center gap-1.5">
              {TOOLS.map((tool) => {
                const on = tools.includes(tool);
                return (
                  <button
                    key={tool}
                    type="button"
                    aria-pressed={on}
                    onClick={() =>
                      setTools(on ? tools.filter((t) => t !== tool) : [...tools, tool])
                    }
                    className={cn(
                      "rounded-full border px-2.5 py-0.5 font-mono text-[11px] transition-colors",
                      on
                        ? "border-transparent bg-primary-soft text-foreground"
                        : "border-border text-muted-foreground hover:bg-secondary",
                    )}
                  >
                    {tool}
                  </button>
                );
              })}
              <Input
                value={extraTools}
                onChange={(e) => setExtraTools(e.target.value)}
                placeholder="more, comma separated"
                className="h-7 w-52 font-mono text-[11px]"
              />
            </div>
          </Field>
          {error && <p className="text-[12px] text-destructive">{error}</p>}
          <div className="flex items-center gap-2">
            <Button type="submit" size="sm" disabled={busy}>
              {busy ? "Creating…" : "Create deployment"}
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

const CUSTOM = " custom"; // sentinel no real name can collide with

/** A select over known choices, with an escape hatch to type anything else.
 *  Empty string means unset, matching how the form serializes options. */
function ChoiceField({
  value,
  onChange,
  choices,
  noneLabel,
  customLabel = "custom…",
}: {
  value: string;
  onChange: (value: string) => void;
  choices: string[];
  noneLabel: string;
  customLabel?: string;
}) {
  const [custom, setCustom] = useState(false);
  if (custom || (value && !choices.includes(value))) {
    return (
      <span className="flex items-center gap-1.5">
        <Input
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="font-mono"
          autoFocus
        />
        <button
          type="button"
          className="text-[11px] text-muted-foreground hover:text-foreground"
          title="Back to the list"
          onClick={() => {
            setCustom(false);
            onChange("");
          }}
        >
          ×
        </button>
      </span>
    );
  }
  return (
    <Select
      value={value}
      className="font-mono"
      onChange={(e) => {
        if (e.target.value === CUSTOM) {
          setCustom(true);
          onChange("");
        } else {
          onChange(e.target.value);
        }
      }}
    >
      <option value="">{noneLabel}</option>
      {choices.map((choice) => (
        <option key={choice} value={choice}>
          {choice}
        </option>
      ))}
      <option value={CUSTOM}>{customLabel}</option>
    </Select>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block space-y-1.5">
      <span className="flex items-baseline gap-1.5">
        <span className="text-[11px] font-medium tracking-[0.08em] text-muted-foreground uppercase">
          {label}
        </span>
        {hint && <span className="text-[10px] text-faint">{hint}</span>}
      </span>
      {children}
    </label>
  );
}
