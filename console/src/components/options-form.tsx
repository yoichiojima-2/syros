"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
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
import { useArtifactSpaces } from "@/lib/hooks";
import { cn } from "@/lib/utils";

/** Edit a stored serialized-AgentOptions dict: the global settings defaults
 *  and a team's option defaults share this form. The options mirror the
 *  AgentOptions subset a session stores and are handed back as that same
 *  serialized dict — the server rejects anything it doesn't recognize rather
 *  than dropping it. */
export function OptionsForm({
  stored,
  description,
  showDescription = false,
  submitLabel,
  onSave,
}: {
  stored: Record<string, unknown>;
  description?: string | null;
  showDescription?: boolean; // adds a description field, saved alongside the options
  submitLabel: string;
  onSave: (options: Record<string, unknown>, description: string) => Promise<string | void>;
}) {
  const spaces = useArtifactSpaces();
  const [desc, setDesc] = useState(description ?? "");
  const [systemPrompt, setSystemPrompt] = useState((stored.system_prompt as string) ?? "");
  const [model, setModel] = useState((stored.model as string) ?? "");
  const [permissionMode, setPermissionMode] = useState((stored.permission_mode as string) ?? "");
  const [artifacts, setArtifacts] = useState(
    typeof stored.artifacts === "string" ? stored.artifacts : "",
  );
  const storedBigquery = Boolean(
    (stored.mcp_servers as Record<string, unknown> | undefined)?.bq,
  );
  const [tools, setTools] = useState<string[]>(
    ((stored.allowed_tools as string[]) ?? []).filter((tool) => TOOLS.includes(tool)),
  );
  const [extraTools, setExtraTools] = useState(
    ((stored.allowed_tools as string[]) ?? [])
      // The auto-allowed BigQuery tool rides the toggle, not the free-text row.
      .filter((tool) => !TOOLS.includes(tool) && !(storedBigquery && tool === BIGQUERY_TOOL))
      .join(", "),
  );
  const [bigquery, setBigquery] = useState(storedBigquery);
  const [connectors, setConnectors] = useState<string[]>((stored.connectors as string[]) ?? []);
  const [budget, setBudget] = useState(
    stored.max_budget_usd == null ? "" : String(stored.max_budget_usd),
  );
  const [maxTurns, setMaxTurns] = useState(stored.max_turns == null ? "" : String(stored.max_turns));
  const [error, setError] = useState("");
  const [flash, setFlash] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    setFlash("");
    // Only send what was filled in: an empty string is "unset", not "".
    const options: Record<string, unknown> = {};
    if (systemPrompt.trim()) options.system_prompt = systemPrompt;
    if (model.trim()) options.model = model.trim();
    if (permissionMode.trim()) options.permission_mode = permissionMode.trim();
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
    if (maxTurns.trim()) options.max_turns = Number(maxTurns);
    try {
      const message = await onSave(options, desc.trim());
      if (message) setFlash(message);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="space-y-4">
      {showDescription && (
        <Field label="Description" hint="for humans">
          <Input
            value={desc}
            onChange={(e) => setDesc(e.target.value)}
            placeholder="what this team is for"
          />
        </Field>
      )}
      <Field label="System prompt">
        <Textarea
          value={systemPrompt}
          onChange={(e) => setSystemPrompt(e.target.value)}
          rows={3}
          placeholder="You are a careful data analyst."
          className="rounded-lg border border-input bg-card px-3 py-2 text-[13px]"
        />
      </Field>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
        <Field label="Model">
          <ChoiceField value={model} onChange={setModel} choices={MODELS} noneLabel="default" />
        </Field>
        <Field label="Permission mode">
          <ChoiceField
            value={permissionMode}
            onChange={setPermissionMode}
            choices={["default", "acceptEdits", "plan", "dontAsk", "bypassPermissions"]}
            noneLabel="default"
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
        <Field label="Max turns">
          <Input
            value={maxTurns}
            onChange={(e) => setMaxTurns(e.target.value)}
            inputMode="numeric"
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
                onClick={() => setTools(on ? tools.filter((t) => t !== tool) : [...tools, tool])}
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
          {busy ? "Saving…" : submitLabel}
        </Button>
        {flash && <span className="text-[11px] text-muted-foreground">{flash}</span>}
      </div>
    </form>
  );
}
