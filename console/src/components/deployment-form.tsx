"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  BIGQUERY_SERVER,
  BIGQUERY_TOOL,
  BigQueryToggle,
  ChoiceField,
  ConnectorPicker,
  Field,
  MODELS,
  TOOLS,
} from "@/components/option-fields";
import { useAgents, useArtifactSpaces, useTeams } from "@/lib/hooks";
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
  const teams = useTeams();
  const spaces = useArtifactSpaces();
  const { agents } = useAgents();
  const [name, setName] = useState("");
  const [agent, setAgent] = useState("");
  const [cron, setCron] = useState("0 9 * * *");
  const [timezone, setTimezone] = useState(browserZone());
  const [prompt, setPrompt] = useState("");
  const [model, setModel] = useState("");
  const [team, setTeam] = useState("");
  const [artifacts, setArtifacts] = useState("");
  const [tools, setTools] = useState<string[]>([]);
  const [extraTools, setExtraTools] = useState("");
  const [connectors, setConnectors] = useState<string[]>([]);
  const [bigquery, setBigquery] = useState(false);
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
    if (team.trim()) options.team = team.trim();
    if (artifacts.trim()) options.artifacts = artifacts.trim();
    const allowed = [
      ...tools,
      ...extraTools
        .split(",")
        .map((tool) => tool.trim())
        .filter((tool) => tool && !tools.includes(tool)),
    ];
    if (bigquery) {
      options.mcp_servers = { bq: BIGQUERY_SERVER };
      if (!allowed.includes(BIGQUERY_TOOL)) allowed.push(BIGQUERY_TOOL);
    }
    if (allowed.length) options.allowed_tools = allowed;
    if (connectors.length) options.connectors = connectors;
    if (budget.trim()) options.max_budget_usd = Number(budget);
    try {
      await post("/api/deployments", {
        name: name.trim(),
        cron: cron.trim(),
        timezone: timezone.trim(),
        prompt,
        agent: agent.trim() || null,
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
              placeholder="Profile the CSVs in the team workspace and write report.md"
              className="rounded-lg border border-input bg-card px-3 py-2 text-[13px]"
            />
          </Field>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
            <Field label="Agent" hint="stored defaults">
              <ChoiceField
                value={agent}
                onChange={setAgent}
                choices={(agents ?? []).map((a) => a.name)}
                noneLabel="none"
              />
            </Field>
            <Field label="Model">
              <ChoiceField
                value={model}
                onChange={setModel}
                choices={MODELS}
                noneLabel="default"
              />
            </Field>
            <Field label="Team">
              <ChoiceField
                value={team}
                onChange={setTeam}
                choices={(teams ?? []).map((t) => t.name)}
                noneLabel="none"
                customLabel="new team…"
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
          <Field label="Connectors" hint="official hosted MCP servers; ∅ = no credential yet">
            <ConnectorPicker value={connectors} onChange={setConnectors} />
          </Field>
          <Field label="BigQuery" hint="read-only SQL; pre-allows its tool">
            <BigQueryToggle on={bigquery} onChange={setBigquery} />
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
