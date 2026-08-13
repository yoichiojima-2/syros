"use client";

import { useState } from "react";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { useArtifactSpaces, useWorkspaces } from "@/lib/hooks";
import { cn } from "@/lib/utils";

// The models people actually pick from; anything else goes through "custom".
const MODELS = ["claude-sonnet-5", "claude-opus-5", "claude-haiku-4-5"];

// The tools people actually allowlist; anything else is typed into "more".
const TOOLS = ["Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebFetch", "WebSearch", "Task"];

/** The run options both forms collect, as the strings the inputs hold. */
export interface RunOptionsState {
  model: string;
  workspace: string;
  artifacts: string;
  budget: string;
  tools: string[];
  extraTools: string;
}

export const EMPTY_RUN_OPTIONS: RunOptionsState = {
  model: "",
  workspace: "",
  artifacts: "",
  budget: "",
  tools: [],
  extraTools: "",
};

/** The serialized AgentOptions dict the server rebuilds — the same shape a
 *  session stores, so the console never tracks which options the sandbox
 *  honours. Only what was filled in is sent: an empty string is "unset". */
export function serializeRunOptions(state: RunOptionsState): Record<string, unknown> {
  const options: Record<string, unknown> = {};
  if (state.model.trim()) options.model = state.model.trim();
  if (state.workspace.trim()) options.workspace = state.workspace.trim();
  if (state.artifacts.trim()) options.artifacts = state.artifacts.trim();
  const allowed = [
    ...state.tools,
    ...state.extraTools
      .split(",")
      .map((tool) => tool.trim())
      .filter((tool) => tool && !state.tools.includes(tool)),
  ];
  if (allowed.length) options.allowed_tools = allowed;
  if (state.budget.trim()) options.max_budget_usd = Number(state.budget);
  return options;
}

/** Model / workspace / artifact space / budget / allowed tools — shared by the
 *  new-session and new-schedule forms, which run the same options. */
export function RunOptionFields({
  value,
  onChange,
}: {
  value: RunOptionsState;
  onChange: (value: RunOptionsState) => void;
}) {
  const workspaces = useWorkspaces();
  const spaces = useArtifactSpaces();
  const set = <K extends keyof RunOptionsState>(key: K, next: RunOptionsState[K]) =>
    onChange({ ...value, [key]: next });

  return (
    <>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Field label="Model">
          <ChoiceField
            value={value.model}
            onChange={(model) => set("model", model)}
            choices={MODELS}
            noneLabel="default"
          />
        </Field>
        <Field label="Workspace">
          <ChoiceField
            value={value.workspace}
            onChange={(workspace) => set("workspace", workspace)}
            choices={(workspaces ?? []).map((w) => w.name)}
            noneLabel="none"
            customLabel="new workspace…"
          />
        </Field>
        <Field label="Artifact space">
          <ChoiceField
            value={value.artifacts}
            onChange={(artifacts) => set("artifacts", artifacts)}
            choices={(spaces ?? []).map((s) => s.name)}
            noneLabel="none"
            customLabel="new space…"
          />
        </Field>
        <Field label="Budget (USD)">
          <Input
            value={value.budget}
            onChange={(e) => set("budget", e.target.value)}
            inputMode="decimal"
            placeholder="none"
            className="font-mono"
          />
        </Field>
      </div>
      <Field label="Allowed tools" hint="click to toggle">
        <div className="flex flex-wrap items-center gap-1.5">
          {TOOLS.map((tool) => {
            const on = value.tools.includes(tool);
            return (
              <button
                key={tool}
                type="button"
                aria-pressed={on}
                onClick={() =>
                  set("tools", on ? value.tools.filter((t) => t !== tool) : [...value.tools, tool])
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
            value={value.extraTools}
            onChange={(e) => set("extraTools", e.target.value)}
            placeholder="more, comma separated"
            className="h-7 w-52 font-mono text-[11px]"
          />
        </div>
      </Field>
    </>
  );
}

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
