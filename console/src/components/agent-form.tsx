"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { ChoiceField, Field, MODELS, TOOLS } from "@/components/option-fields";
import { useArtifactSpaces, useWorkspaces } from "@/lib/hooks";
import { post } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { AgentSummary } from "@/lib/types";

/** Create or edit a stored agent (persona). Its options mirror the
 *  AgentOptions subset a session stores and are posted as that same serialized
 *  dict — the server rejects anything it doesn't recognize rather than
 *  dropping it. */
export function AgentForm({
  agent,
  onSaved,
  onCancel,
}: {
  agent?: AgentSummary; // present = edit in place, absent = create
  onSaved: (name: string) => void;
  onCancel: () => void;
}) {
  const workspaces = useWorkspaces();
  const spaces = useArtifactSpaces();
  const stored = agent?.options ?? {};
  const [name, setName] = useState(agent?.name ?? "");
  const [description, setDescription] = useState(agent?.description ?? "");
  const [systemPrompt, setSystemPrompt] = useState((stored.system_prompt as string) ?? "");
  const [model, setModel] = useState((stored.model as string) ?? "");
  const [permissionMode, setPermissionMode] = useState((stored.permission_mode as string) ?? "");
  const [workspace, setWorkspace] = useState((stored.workspace as string) ?? "");
  const [artifacts, setArtifacts] = useState(
    typeof stored.artifacts === "string" ? stored.artifacts : "",
  );
  const [tools, setTools] = useState<string[]>(
    ((stored.allowed_tools as string[]) ?? []).filter((tool) => TOOLS.includes(tool)),
  );
  const [extraTools, setExtraTools] = useState(
    ((stored.allowed_tools as string[]) ?? []).filter((tool) => !TOOLS.includes(tool)).join(", "),
  );
  const [budget, setBudget] = useState(
    stored.max_budget_usd == null ? "" : String(stored.max_budget_usd),
  );
  const [maxTurns, setMaxTurns] = useState(stored.max_turns == null ? "" : String(stored.max_turns));
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    // Only send what was filled in: an empty string is "unset", not "".
    const options: Record<string, unknown> = {};
    if (systemPrompt.trim()) options.system_prompt = systemPrompt;
    if (model.trim()) options.model = model.trim();
    if (permissionMode.trim()) options.permission_mode = permissionMode.trim();
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
    if (maxTurns.trim()) options.max_turns = Number(maxTurns);
    try {
      const body = { name: name.trim(), description: description.trim(), options };
      if (agent) {
        await post(`/api/agents/${encodeURIComponent(agent.name)}/update`, body);
      } else {
        await post("/api/agents", body);
      }
      onSaved(name.trim());
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>{agent ? `Edit ${agent.name}` : "New agent"}</CardTitle>
        <CardDescription>
          A stored run configuration: sessions and deployments that reference it get these options
          as defaults. Edits apply to future runs only.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={submit} className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-3">
            <Field label="Name" hint="lowercase, no spaces">
              <Input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="code-reviewer"
                autoFocus={!agent}
                disabled={!!agent}
                required
              />
            </Field>
            <Field label="Description" hint="for humans">
              <Input
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="careful code reviews"
              />
            </Field>
            <Field label="Permission mode">
              <ChoiceField
                value={permissionMode}
                onChange={setPermissionMode}
                choices={["default", "acceptEdits", "plan", "dontAsk", "bypassPermissions"]}
                noneLabel="default"
              />
            </Field>
          </div>
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
              {busy ? "Saving…" : agent ? "Save agent" : "Create agent"}
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
