"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
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
import { useArtifactSpaces } from "@/lib/hooks";

/** Edit a stored serialized-AgentOptions dict: the global settings defaults
 *  and a workspace's option defaults share this form. The options mirror the
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
  const draft = useOptionsDraft(stored);
  const [desc, setDesc] = useState(description ?? "");
  const [error, setError] = useState("");
  const [flash, setFlash] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    setFlash("");
    try {
      const message = await onSave(buildOptionsPayload(draft), desc.trim());
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
            placeholder="what this workspace is for"
          />
        </Field>
      )}
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
        <Field label="Permission mode">
          <ChoiceField
            value={draft.permissionMode}
            onChange={draft.setPermissionMode}
            choices={["default", "acceptEdits", "plan", "dontAsk", "bypassPermissions"]}
            noneLabel="default"
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
          {busy ? "Saving…" : submitLabel}
        </Button>
        {flash && <span className="text-[11px] text-muted-foreground">{flash}</span>}
      </div>
    </form>
  );
}
