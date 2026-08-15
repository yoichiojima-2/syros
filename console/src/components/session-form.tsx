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

/** New-session form: a prompt plus the same run options a deployment carries.
 *  Starting one here is what a client's query() does — the session is ordinary,
 *  and the transcript takes over from the moment the job is triggered. */
export function SessionForm({
  onCreated,
  onCancel,
}: {
  onCreated: (sessionId: string) => void;
  onCancel: () => void;
}) {
  const teams = useTeams();
  const spaces = useArtifactSpaces();
  const { agents } = useAgents();
  const [prompt, setPrompt] = useState("");
  const [agent, setAgent] = useState("");
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
    if (busy || !prompt.trim()) return;
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
      const { session_id } = await post<{ session_id: string }>("/api/sessions", {
        prompt: prompt.trim(),
        agent: agent.trim() || null,
        options,
      });
      onCreated(session_id);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>New session</CardTitle>
        <CardDescription>
          Starts a sandbox run now. Follow-up prompts go to the same session from its transcript.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={submit} className="space-y-4">
          <Field label="Prompt">
            <Textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              onKeyDown={(e) => {
                // ⌘/Ctrl+Enter submits; plain Enter stays a newline, since the
                // options below are part of the same decision.
                if (e.key === "Enter" && (e.metaKey || e.ctrlKey))
                  e.currentTarget.form?.requestSubmit();
              }}
              rows={3}
              required
              autoFocus
              placeholder="Profile the CSVs in the team workspace and write report.md"
              className="rounded-lg border border-input bg-card px-3 py-2 text-[13px] leading-relaxed"
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
            <Button type="submit" size="sm" disabled={busy || !prompt.trim()}>
              {busy ? "Starting…" : "Start session"}
            </Button>
            <Button type="button" variant="ghost" size="sm" onClick={onCancel}>
              Cancel
            </Button>
            <span className="text-[11px] text-muted-foreground">
              Unlisted tools pause for approval.
            </span>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}
