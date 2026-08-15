"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  allowedTools,
  BigQueryToggle,
  buildOptionsPayload,
  ChoiceField,
  ConnectorPicker,
  Field,
  MODELS,
  ToolPicker,
  useOptionsDraft,
} from "@/components/option-fields";
import { useAgents, useArtifactSpaces, useWorkspaces } from "@/lib/hooks";
import { post } from "@/lib/api";
import { defaultPrompt, isDefaultPrompt } from "@/lib/types";
import { cn } from "@/lib/utils";

type Mode = "agent" | "default" | "custom";

const TABS: [Mode, string][] = [
  ["agent", "Agent"],
  ["default", "Default"],
  ["custom", "Custom"],
];

/** New-session form: a prompt plus one of three ways to configure the run — a
 *  stored agent, the default agent, or hand-picked options. The tabs are
 *  exclusive because the backend merge treats any explicitly-set field as a
 *  whole-value override of the agent's (a single tool chip would silently
 *  replace the agent's entire allowlist), so the agent tab sends the agent's
 *  stored options untouched and shows them read-only instead. Starting a
 *  session here is what a client's query() does — the session is ordinary, and
 *  the transcript takes over once the job runs.
 *
 *  Passing `agent` pins the form to that agent: the tabs and the picker drop
 *  away, since the caller (the agent page) already made that choice and all
 *  that's left to decide is the prompt. */
export function SessionForm({
  onCreated,
  onCancel,
  agent: pinned,
}: {
  onCreated: (sessionId: string) => void;
  onCancel: () => void;
  agent?: string;
}) {
  const workspaces = useWorkspaces();
  const spaces = useArtifactSpaces();
  const { agents } = useAgents();
  const draft = useOptionsDraft();
  const [mode, setMode] = useState<Mode>("agent");
  const [prompt, setPrompt] = useState("");
  const [agent, setAgent] = useState(pinned ?? "");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  // With no stored agents the agent tab is a dead end, so land on Default.
  const noAgents = agents !== null && agents.length === 0;
  useEffect(() => {
    if (noAgents && !pinned) setMode("default");
  }, [noAgents, pinned]);

  const selectedAgent = (agents ?? []).find((a) => a.name === agent) ?? null;
  // Only the agent tab needs more than a prompt.
  const ready = prompt.trim() && (mode !== "agent" || agent.trim());

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (busy || !ready) return;
    setBusy(true);
    setError("");
    // On the agent tab the run options stay with the agent; only the budget
    // rides along, since it's a per-run cap rather than part of the persona.
    // The Default tab sends only the fields it renders — a half-filled custom
    // tab left behind in the same draft must not leak into it.
    const options: Record<string, unknown> = {};
    if (mode === "custom") {
      Object.assign(options, buildOptionsPayload(draft));
    } else {
      if (mode === "default") {
        // Nothing to send for the prompt itself: options with no system prompt
        // resolve to the default one server-side. Extra instructions are the
        // exception — they ride the preset, so they append instead of replace.
        if (draft.systemPrompt.trim()) options.system_prompt = defaultPrompt(draft.systemPrompt);
        if (draft.workspace.trim()) options.workspace = draft.workspace.trim();
        const allowed = allowedTools(draft);
        if (allowed.length) options.allowed_tools = allowed;
      }
      if (draft.budget.trim()) options.max_budget_usd = Number(draft.budget);
    }
    try {
      const { session_id } = await post<{ session_id: string }>("/api/sessions", {
        prompt: prompt.trim(),
        agent: mode === "agent" ? agent.trim() : null,
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
        <CardTitle>{pinned ? `New session as ${pinned}` : "New session"}</CardTitle>
        <CardDescription>
          Starts a sandbox run now. Follow-up prompts go to the same session from its transcript.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={submit} className="space-y-4">
          {!pinned && (
            <div
              className="flex items-center gap-1 rounded-lg bg-secondary p-0.5 w-fit"
              role="tablist"
            >
              {TABS.map(([tab, label]) => (
                <button
                  key={tab}
                  type="button"
                  role="tab"
                  aria-selected={mode === tab}
                  onClick={() => setMode(tab)}
                  className={cn(
                    "rounded-md px-3 py-1 text-[12px] font-medium transition-colors",
                    mode === tab
                      ? "bg-card text-foreground shadow-sm"
                      : "text-muted-foreground hover:text-foreground",
                  )}
                >
                  {label}
                </button>
              ))}
            </div>
          )}
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
              placeholder="Profile the CSVs in the workspace and write report.md"
              className="rounded-lg border border-input bg-card px-3 py-2 text-[13px] leading-relaxed"
            />
          </Field>
          {mode === "agent" ? (
            <>
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
                {/* Pinned, the agent is already named in the title — only the
                    budget is still open. */}
                {!pinned && (
                  <Field label="Agent" hint="runs with its stored options">
                    <ChoiceField
                      value={agent}
                      onChange={setAgent}
                      choices={(agents ?? []).map((a) => a.name)}
                      noneLabel="pick one…"
                    />
                  </Field>
                )}
                <Field label="Budget (USD)" hint="per-run cap">
                  <Input
                    value={draft.budget}
                    onChange={(e) => draft.setBudget(e.target.value)}
                    inputMode="decimal"
                    placeholder="none"
                    className="font-mono"
                  />
                </Field>
              </div>
              {selectedAgent && <AgentOptionsSummary options={selectedAgent.options} />}
            </>
          ) : mode === "default" ? (
            <>
              <p className="text-[12px] leading-relaxed text-muted-foreground">
                Runs the default agent — no stored persona in front of it. Everything below is
                optional.
              </p>
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                <Field label="Workspace" hint="shared directory">
                  <ChoiceField
                    value={draft.workspace}
                    onChange={draft.setWorkspace}
                    choices={(workspaces ?? []).map((t) => t.name)}
                    noneLabel="none"
                    customLabel="new workspace…"
                  />
                </Field>
                <Field label="Budget (USD)" hint="per-run cap">
                  <Input
                    value={draft.budget}
                    onChange={(e) => draft.setBudget(e.target.value)}
                    inputMode="decimal"
                    placeholder="none"
                    className="font-mono"
                  />
                </Field>
              </div>
              <Field label="Extra instructions" hint="appended to the default prompt">
                <Textarea
                  value={draft.systemPrompt}
                  onChange={(e) => draft.setSystemPrompt(e.target.value)}
                  rows={2}
                  placeholder="Prefer small commits. (optional)"
                  className="rounded-lg border border-input bg-card px-3 py-2 text-[13px]"
                />
              </Field>
              <Field label="Allowed tools" hint="click to toggle">
                <ToolPicker draft={draft} />
              </Field>
            </>
          ) : (
            <>
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
              <Field label="Allowed tools" hint="click to toggle">
                <ToolPicker draft={draft} />
              </Field>
              <Field label="Connectors" hint="official hosted MCP servers; ∅ = no credential yet">
                <ConnectorPicker value={draft.connectors} onChange={draft.setConnectors} />
              </Field>
              <Field label="BigQuery" hint="read-only SQL; pre-allows its tool">
                <BigQueryToggle on={draft.bigquery} onChange={draft.setBigquery} />
              </Field>
            </>
          )}
          {error && <p className="text-[12px] text-destructive">{error}</p>}
          <div className="flex items-center gap-2">
            <Button type="submit" size="sm" disabled={busy || !ready}>
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

/** Read-only view of the agent's stored options — what the run will actually
 *  get. Editing them belongs on the agent page, not here. */
function AgentOptionsSummary({ options }: { options: Record<string, unknown> }) {
  const str = (key: string) => {
    const value = options[key];
    return typeof value === "string" && value ? value : null;
  };
  const list = (key: string) => {
    const value = options[key];
    return Array.isArray(value) && value.length ? (value as string[]) : null;
  };
  // A preset prompt is the harness's own, so what's worth showing is the fact
  // of it plus whatever the agent appended.
  const preset = isDefaultPrompt(options.system_prompt);
  const systemPrompt = preset
    ? (options.system_prompt as { append?: string }).append || null
    : str("system_prompt");
  const rows: [string, string][] = [];
  const push = (label: string, value: string | null) => {
    if (value) rows.push([label, value]);
  };
  if (preset) push("system prompt", "the default prompt");
  push("model", str("model"));
  push("permission mode", str("permission_mode"));
  push("workspace", str("workspace"));
  push("artifacts", str("artifacts"));
  push("allowed tools", list("allowed_tools")?.join(", ") ?? null);
  push("disallowed tools", list("disallowed_tools")?.join(", ") ?? null);
  push("connectors", list("connectors")?.join(", ") ?? null);
  const servers = options.mcp_servers;
  if (servers && typeof servers === "object" && Object.keys(servers).length)
    push("mcp servers", Object.keys(servers).join(", "));
  if (typeof options.max_turns === "number") push("max turns", String(options.max_turns));
  if (typeof options.max_budget_usd === "number")
    push("budget (USD)", String(options.max_budget_usd));

  return (
    <div className="space-y-2 rounded-lg border border-border bg-secondary/40 p-3">
      {systemPrompt && (
        <p className="line-clamp-3 text-[12px] leading-relaxed text-muted-foreground whitespace-pre-wrap">
          {systemPrompt}
        </p>
      )}
      {rows.length > 0 && (
        <dl className="grid gap-x-4 gap-y-1 text-[11px] sm:grid-cols-[auto_1fr]">
          {rows.map(([label, value]) => (
            <div key={label} className="contents">
              <dt className="tracking-[0.08em] text-faint uppercase">{label}</dt>
              <dd className="font-mono text-muted-foreground break-words">{value}</dd>
            </div>
          ))}
        </dl>
      )}
      {!systemPrompt && rows.length === 0 && (
        <p className="text-[12px] text-faint">This agent stores no options — runs use defaults.</p>
      )}
    </div>
  );
}
