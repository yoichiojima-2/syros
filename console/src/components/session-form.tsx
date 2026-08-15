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
  ConnectorPicker,
  Field,
  MODELS,
  ToolPicker,
  useOptionsDraft,
} from "@/components/option-fields";
import { useAgents, useArtifactSpaces, useWorkspaces } from "@/lib/hooks";
import { post } from "@/lib/api";

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
  const workspaces = useWorkspaces();
  const spaces = useArtifactSpaces();
  const { agents } = useAgents();
  const draft = useOptionsDraft();
  const [prompt, setPrompt] = useState("");
  const [agent, setAgent] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (busy || !prompt.trim()) return;
    setBusy(true);
    setError("");
    try {
      const { session_id } = await post<{ session_id: string }>("/api/sessions", {
        prompt: prompt.trim(),
        agent: agent.trim() || null,
        options: buildOptionsPayload(draft),
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
              placeholder="Profile the CSVs in the workspace and write report.md"
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
