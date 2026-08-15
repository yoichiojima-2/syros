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
// sandbox swaps in the live server. Toggling it on also pre-allows its tool —
// the toggle is a one-click opt-in, not a capability that then waits for
// approval. Queries still need the deployment's IAM opt-in (sandbox_bigquery).
export const BIGQUERY_SERVER = { type: "builtin", name: "bigquery" };
export const BIGQUERY_TOOL = "mcp__bq__query";

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

/** One pill for the built-in BigQuery tool, styled like the connector chips.
 *  On means the form submits mcp_servers={bq: BIGQUERY_SERVER} and pre-allows
 *  BIGQUERY_TOOL. Read access itself is the deployment's call: without
 *  `sandbox_bigquery = true` in Terraform every query returns a permission
 *  error, which is why the hint rides on the pill. */
export function BigQueryToggle({
  on,
  onChange,
}: {
  on: boolean;
  onChange: (on: boolean) => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <button
        type="button"
        aria-pressed={on}
        title="Read-only SQL over the project's BigQuery — needs sandbox_bigquery = true in the deployment"
        onClick={() => onChange(!on)}
        className={cn(
          "rounded-full border px-2.5 py-0.5 font-mono text-[11px] transition-colors",
          on
            ? "border-transparent bg-primary-soft text-foreground"
            : "border-border text-muted-foreground hover:bg-secondary",
        )}
      >
        bigquery
      </button>
    </div>
  );
}

/** Shared draft state over a stored serialized-AgentOptions dict. Every form
 *  renders only the fields it cares about; unrendered fields stay empty and
 *  buildOptionsPayload leaves them out of the payload. */
export function useOptionsDraft(stored: Record<string, unknown> = {}) {
  const storedBigquery = Boolean(
    (stored.mcp_servers as Record<string, unknown> | undefined)?.bq,
  );
  // The system prompt is either a hand-written persona or the harness's own
  // prompt with instructions appended, so the toggle and the text are one
  // field split in two: with the toggle on, the text is what gets appended.
  const storedDefault = isDefaultPrompt(stored.system_prompt);
  const [defaults, setDefaults] = useState(storedDefault);
  const [systemPrompt, setSystemPrompt] = useState(
    (storedDefault
      ? (stored.system_prompt as DefaultPrompt).append
      : (stored.system_prompt as string)) ?? "",
  );
  const [model, setModel] = useState((stored.model as string) ?? "");
  const [permissionMode, setPermissionMode] = useState((stored.permission_mode as string) ?? "");
  const [workspace, setWorkspace] = useState(((stored.workspace ?? stored.team) as string) ?? "");
  const [artifacts, setArtifacts] = useState(
    typeof stored.artifacts === "string" ? stored.artifacts : "",
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
  return {
    defaults, setDefaults,
    systemPrompt, setSystemPrompt,
    model, setModel,
    permissionMode, setPermissionMode,
    workspace, setWorkspace,
    artifacts, setArtifacts,
    tools, setTools,
    extraTools, setExtraTools,
    bigquery, setBigquery,
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
  if (draft.defaults) options.system_prompt = defaultPrompt(draft.systemPrompt);
  else if (draft.systemPrompt.trim()) options.system_prompt = draft.systemPrompt;
  if (draft.model.trim()) options.model = draft.model.trim();
  if (draft.permissionMode.trim()) options.permission_mode = draft.permissionMode.trim();
  if (draft.workspace.trim()) options.workspace = draft.workspace.trim();
  if (draft.artifacts.trim()) options.artifacts = draft.artifacts.trim();
  const allowed = allowedTools(draft);
  if (draft.bigquery) {
    options.mcp_servers = { bq: BIGQUERY_SERVER };
    if (!allowed.includes(BIGQUERY_TOOL)) allowed.push(BIGQUERY_TOOL);
  }
  if (allowed.length) options.allowed_tools = allowed;
  if (draft.connectors.length) options.connectors = draft.connectors;
  if (draft.budget.trim()) options.max_budget_usd = Number(draft.budget);
  if (draft.maxTurns.trim()) options.max_turns = Number(draft.maxTurns);
  return options;
}

/** The system-prompt field: a persona, or the default agent's own prompt.
 *
 *  Empty and toggled off, a run has no system prompt at all — a bare
 *  assistant. The toggle is how you ask for the harness's default prompt
 *  instead, and the textarea then holds instructions appended after it rather
 *  than a replacement for it. */
export function SystemPromptField({ draft }: { draft: OptionsDraft }) {
  const { defaults, setDefaults, systemPrompt, setSystemPrompt } = draft;
  return (
    <Field
      label="System prompt"
      hint={defaults ? "appended to the default prompt" : "replaces it entirely"}
    >
      <div className="space-y-1.5">
        <DefaultPromptToggle on={defaults} onChange={setDefaults} />
        <Textarea
          value={systemPrompt}
          onChange={(e) => setSystemPrompt(e.target.value)}
          rows={3}
          placeholder={
            defaults ? "Prefer small commits. (optional)" : "You are a careful data analyst."
          }
          className="rounded-lg border border-input bg-card px-3 py-2 text-[13px]"
        />
      </div>
    </Field>
  );
}

/** One pill for "run the default agent", styled like the connector chips. */
export function DefaultPromptToggle({
  on,
  onChange,
}: {
  on: boolean;
  onChange: (on: boolean) => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={on}
      title="Run with the harness's default system prompt — the stock agent, no persona of its own"
      onClick={() => onChange(!on)}
      className={cn(
        "rounded-full border px-2.5 py-0.5 font-mono text-[11px] transition-colors",
        on
          ? "border-transparent bg-primary-soft text-foreground"
          : "border-border text-muted-foreground hover:bg-secondary",
      )}
    >
      default prompt
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
