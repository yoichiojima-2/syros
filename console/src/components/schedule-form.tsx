"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { post } from "@/lib/api";
import { cn } from "@/lib/utils";

// The presets cover what people actually schedule; anything else is typed in.
const PRESETS: { label: string; cron: string }[] = [
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

/** New-schedule form. Run options mirror the AgentOptions subset a session
 *  stores, and are posted as that same serialized dict — the server rejects
 *  anything it doesn't recognize rather than dropping it. */
export function ScheduleForm({
  onCreated,
  onCancel,
}: {
  onCreated: (name: string) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState("");
  const [cron, setCron] = useState("0 9 * * *");
  const [timezone, setTimezone] = useState(browserZone());
  const [prompt, setPrompt] = useState("");
  const [model, setModel] = useState("");
  const [workspace, setWorkspace] = useState("");
  const [artifacts, setArtifacts] = useState("");
  const [tools, setTools] = useState("");
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
    if (tools.trim()) {
      options.allowed_tools = tools
        .split(",")
        .map((tool) => tool.trim())
        .filter(Boolean);
    }
    if (budget.trim()) options.max_budget_usd = Number(budget);
    try {
      await post("/api/schedules", {
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
        <CardTitle>New schedule</CardTitle>
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
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
            <Field label="Model">
              <Input
                value={model}
                onChange={(e) => setModel(e.target.value)}
                placeholder="default"
                className="font-mono"
              />
            </Field>
            <Field label="Workspace">
              <Input
                value={workspace}
                onChange={(e) => setWorkspace(e.target.value)}
                placeholder="none"
                className="font-mono"
              />
            </Field>
            <Field label="Artifact space">
              <Input
                value={artifacts}
                onChange={(e) => setArtifacts(e.target.value)}
                placeholder="none"
                className="font-mono"
              />
            </Field>
            <Field label="Allowed tools" hint="comma separated">
              <Input
                value={tools}
                onChange={(e) => setTools(e.target.value)}
                placeholder="Read, Write, Bash"
                className="font-mono"
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
          {error && <p className="text-[12px] text-destructive">{error}</p>}
          <div className="flex items-center gap-2">
            <Button type="submit" size="sm" disabled={busy}>
              {busy ? "Creating…" : "Create schedule"}
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
