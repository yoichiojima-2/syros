"use client";

import { useState } from "react";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { useConnectors } from "@/lib/hooks";
import { type DefaultPrompt, defaultPrompt, isDefaultPrompt } from "@/lib/types";
import { cn } from "@/lib/utils";

// The models people actually pick from; anything else goes through "custom".
export const MODELS = ["claude-sonnet-5", "claude-opus-5", "claude-haiku-4-5"];

// The tools people actually allowlist; anything else is typed into "more".
export const TOOLS = [
  "Read",
  "Write",
  "Edit",
  "Bash",
  "Glob",
  "Grep",
  "WebFetch",
  "WebSearch",
  "Task",
];

// The built-in BigQuery MCP server: a session asks for it by reference and the
// sandbox swaps in the live server. Toggling it on also pre-allows its tools —
// the toggle is a one-click opt-in, not a capability that then waits for
// approval. Queries still need the deployment's IAM opt-in (sandbox_bigquery),
// and `write` needs its own (sandbox_bigquery_write).
export const BIGQUERY_SERVER = { type: "builtin", name: "bigquery" };
export const BIGQUERY_WRITE_SERVER = { type: "builtin", name: "bigquery", write: true };
export const BIGQUERY_TOOL = "mcp__bq__query";
// Mirrors options.BIGQUERY_WRITE_TOOLS: the tools the write reference adds.
export const BIGQUERY_WRITE_TOOLS = [
  "mcp__bq__tables",
  "mcp__bq__create_table",
  "mcp__bq__insert",
  "mcp__bq__query_into",
  "mcp__bq__drop_table",
];

const CUSTOM = " custom"; // sentinel no real name can collide with

/** A select over known choices, with an escape hatch to type anything else.
 *  Empty string means unset, matching how the forms serialize options. */
export function ChoiceField({
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

/** Toggle-chip row over the connector catalog (platforms whose official
 *  hosted MCP servers a session can mount). Unconfigured connectors stay
 *  selectable — the credential can be stored later, before the run. */
export function ConnectorPicker({
  value,
  onChange,
}: {
  value: string[];
  onChange: (value: string[]) => void;
}) {
  const connectors = useConnectors();
  if (connectors === null) return null;
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {connectors.map((connector) => {
        const on = value.includes(connector.name);
        return (
          <button
            key={connector.name}
            type="button"
            aria-pressed={on}
            title={
              connector.configured
                ? connector.label
                : `${connector.label} — no credential yet (syros connectors ${
                    connector.auth === "token" ? "set" : "auth"
                  } ${connector.name})`
            }
            onClick={() =>
              onChange(on ? value.filter((n) => n !== connector.name) : [...value, connector.name])
            }
            className={cn(
              "rounded-full border px-2.5 py-0.5 font-mono text-[11px] transition-colors",
              on
                ? "border-transparent bg-primary-soft text-foreground"
                : "border-border text-muted-foreground hover:bg-secondary",
            )}
          >
            {connector.name}
            {!connector.configured && <span className="text-faint"> ∅</span>}
          </button>
        );
      })}
    </div>
  );
}

/** Two pills for the built-in BigQuery tools, styled like the connector chips.
 *  `bigquery` submits mcp_servers={bq: BIGQUERY_SERVER} and pre-allows
 *  BIGQUERY_TOOL; `write` upgrades that reference and pre-allows the tools that
 *  keep tables in the agents' own dataset. Access itself is the deployment's
 *  call — without `sandbox_bigquery` / `sandbox_bigquery_write` in Terraform
 *  the calls come back as permission errors, which is why the hints ride on the
 *  pills. Write implies read: they are the same server. */
export function BigQueryToggle({
  on,
  onChange,
  write,
  onWriteChange,
}: {
  on: boolean;
  onChange: (on: boolean) => void;
  write: boolean;
  onWriteChange: (on: boolean) => void;
}) {
  const pill = (active: boolean) =>
    cn(
      "rounded-full border px-2.5 py-0.5 font-mono text-[11px] transition-colors",
      active
        ? "border-transparent bg-primary-soft text-foreground"
        : "border-border text-muted-foreground hover:bg-secondary",
    );
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <button
        type="button"
        aria-pressed={on}
        title="Read-only SQL over the project's BigQuery — needs sandbox_bigquery = true in the deployment"
        onClick={() => {
          // Turning the server off takes write with it: write is a key on this
          // same reference, not a second server.
          if (on) onWriteChange(false);
          onChange(!on);
        }}
        className={pill(on)}
      >
        bigquery
      </button>
      <button
        type="button"
        aria-pressed={write}
        title="Let the session keep its own tables in the agent dataset — needs sandbox_bigquery_write = true in the deployment"
        onClick={() => {
          if (!write) onChange(true);
          onWriteChange(!write);
        }}
        className={pill(write)}
      >
        write
      </button>
    </div>
  );
}

/** Shared draft state over a stored serialized-AgentOptions dict. Every form
 *  renders only the fields it cares about; unrendered fields stay empty and
 *  buildOptionsPayload leaves them out of the payload. */
export function useOptionsDraft(stored: Record<string, unknown> = {}) {
  const storedBq = (stored.mcp_servers as Record<string, unknown> | undefined)?.bq as
    | Record<string, unknown>
    | undefined;
  const storedBigquery = Boolean(storedBq);
  const storedBigqueryWrite = Boolean(storedBq?.write);
  // The system prompt is either a hand-written persona that replaces the
  // default prompt or text added after it, so the toggle and the text are one
  // field split in two: with the toggle on, the text is what gets appended.
  const storedAppend = isDefaultPrompt(stored.system_prompt);
  const [append, setAppend] = useState(storedAppend);
  const [systemPrompt, setSystemPrompt] = useState(
    (storedAppend
      ? (stored.system_prompt as DefaultPrompt).append
      : (stored.system_prompt as string)) ?? "",
  );
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
    ((stored.allowed_tools as string[]) ?? [])
      // The auto-allowed BigQuery tools ride the toggles, not the free-text row.
      .filter(
        (tool) =>
          !TOOLS.includes(tool) &&
          !(storedBigquery && tool === BIGQUERY_TOOL) &&
          !(storedBigqueryWrite && BIGQUERY_WRITE_TOOLS.includes(tool)),
      )
      .join(", "),
  );
  const [bigquery, setBigquery] = useState(storedBigquery);
  const [bigqueryWrite, setBigqueryWrite] = useState(storedBigqueryWrite);
  const [connectors, setConnectors] = useState<string[]>((stored.connectors as string[]) ?? []);
  const [budget, setBudget] = useState(
    stored.max_budget_usd == null ? "" : String(stored.max_budget_usd),
  );
  const [maxTurns, setMaxTurns] = useState(stored.max_turns == null ? "" : String(stored.max_turns));
  return {
    append, setAppend,
    systemPrompt, setSystemPrompt,
    model, setModel,
    permissionMode, setPermissionMode,
    workspace, setWorkspace,
    artifacts, setArtifacts,
    tools, setTools,
    extraTools, setExtraTools,
    bigquery, setBigquery,
    bigqueryWrite, setBigqueryWrite,
    connectors, setConnectors,
    budget, setBudget,
    maxTurns, setMaxTurns,
  };
}

export type OptionsDraft = ReturnType<typeof useOptionsDraft>;

/** The tool allowlist a draft submits: the chips plus the free-text row. */
export function allowedTools(draft: OptionsDraft): string[] {
  return [
    ...draft.tools,
    ...draft.extraTools
      .split(",")
      .map((tool) => tool.trim())
      .filter((tool) => tool && !draft.tools.includes(tool)),
  ];
}

/** The serialized dict a draft submits. Only what was filled in rides the
 *  payload: an empty string is "unset", not "". */
export function buildOptionsPayload(draft: OptionsDraft): Record<string, unknown> {
  const options: Record<string, unknown> = {};
  // The preset rides the payload even with no text behind it: unset inherits
  // whatever persona a workspace or the global settings stores, while the
  // preset says "the default prompt, whatever those layers hold".
  if (draft.append) options.system_prompt = defaultPrompt(draft.systemPrompt);
  else if (draft.systemPrompt.trim()) options.system_prompt = draft.systemPrompt;
  if (draft.model.trim()) options.model = draft.model.trim();
  if (draft.permissionMode.trim()) options.permission_mode = draft.permissionMode.trim();
  if (draft.workspace.trim()) options.workspace = draft.workspace.trim();
  if (draft.artifacts.trim()) options.artifacts = draft.artifacts.trim();
  const allowed = allowedTools(draft);
  if (draft.bigquery || draft.bigqueryWrite) {
    const write = draft.bigqueryWrite;
    options.mcp_servers = { bq: write ? BIGQUERY_WRITE_SERVER : BIGQUERY_SERVER };
    const tools = write ? [BIGQUERY_TOOL, ...BIGQUERY_WRITE_TOOLS] : [BIGQUERY_TOOL];
    for (const tool of tools) if (!allowed.includes(tool)) allowed.push(tool);
  }
  if (allowed.length) options.allowed_tools = allowed;
  if (draft.connectors.length) options.connectors = draft.connectors;
  if (draft.budget.trim()) options.max_budget_usd = Number(draft.budget);
  if (draft.maxTurns.trim()) options.max_turns = Number(draft.maxTurns);
  return options;
}

/** The system-prompt field. Left empty the run gets the default prompt, so
 *  what this field holds is a persona that replaces it; the `append` toggle
 *  keeps the default prompt and adds the text after it instead. */
export function SystemPromptField({ draft }: { draft: OptionsDraft }) {
  const { append, setAppend, systemPrompt, setSystemPrompt } = draft;
  return (
    <Field
      label="System prompt"
      hint={
        append ? "added after the default prompt" : "empty = the default prompt; text replaces it"
      }
    >
      <div className="space-y-1.5">
        <AppendToggle on={append} onChange={setAppend} />
        <Textarea
          value={systemPrompt}
          onChange={(e) => setSystemPrompt(e.target.value)}
          rows={3}
          placeholder={append ? "Prefer small commits." : "You are a careful data analyst."}
          className="rounded-lg border border-input bg-card px-3 py-2 text-[13px]"
        />
      </div>
    </Field>
  );
}

/** One pill for "add to the default prompt instead of replacing it", styled
 *  like the connector chips. */
export function AppendToggle({ on, onChange }: { on: boolean; onChange: (on: boolean) => void }) {
  return (
    <button
      type="button"
      aria-pressed={on}
      title="Keep the default system prompt and add these instructions after it"
      onClick={() => onChange(!on)}
      className={cn(
        "rounded-full border px-2.5 py-0.5 font-mono text-[11px] transition-colors",
        on
          ? "border-transparent bg-primary-soft text-foreground"
          : "border-border text-muted-foreground hover:bg-secondary",
      )}
    >
      append
    </button>
  );
}

/** Toggle-chip row over the common tools plus a free-text row for the rest —
 *  the body of every form's "Allowed tools" field. */
export function ToolPicker({ draft }: { draft: OptionsDraft }) {
  const { tools, setTools, extraTools, setExtraTools } = draft;
  return (
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
  );
}

export function Field({
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
