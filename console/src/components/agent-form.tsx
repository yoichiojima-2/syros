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
  SystemPromptField,
  ToolPicker,
  useOptionsDraft,
} from "@/components/option-fields";
import { useArtifactSpaces, useWorkspaces } from "@/lib/hooks";
import { post } from "@/lib/api";
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
  const draft = useOptionsDraft(agent?.options ?? {});
  const [name, setName] = useState(agent?.name ?? "");
  const [description, setDescription] = useState(agent?.description ?? "");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const body = {
        name: name.trim(),
        description: description.trim(),
        options: buildOptionsPayload(draft),
      };
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
          A stored run configuration: sessions and workflow tasks that reference it get these options
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
                value={draft.permissionMode}
                onChange={draft.setPermissionMode}
                choices={["default", "acceptEdits", "plan", "dontAsk", "bypassPermissions"]}
                noneLabel="default"
              />
            </Field>
          </div>
          <SystemPromptField draft={draft} />
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
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
            <Field label="Max turns">
              <Input
                value={draft.maxTurns}
                onChange={(e) => draft.setMaxTurns(e.target.value)}
                inputMode="numeric"
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
