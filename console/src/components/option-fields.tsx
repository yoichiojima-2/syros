"use client";

import { useState } from "react";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { useConnectors } from "@/lib/hooks";
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
