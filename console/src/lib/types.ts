// Shapes of the JSON payloads served by src/syros/console/api.py.
// All timestamps are epoch seconds (the server flattens Firestore datetimes),
// and the message/block shapes mirror syros.types' Firestore serialization.

// Mirrors derived_state() in src/syros/console/api.py — keep the two in sync.
export type SessionState =
  | "running"
  | "starting"
  | "stalled"
  | "queued"
  | "idle"
  | "terminated"
  | "unknown";

export const ACTIVE_STATES: ReadonlySet<SessionState> = new Set([
  "running",
  "starting",
  "queued",
  "stalled",
]);

export interface SessionSummary {
  id: string;
  status: string | null;
  state: SessionState; // derived liveness, not raw status
  disabled: boolean;
  stop_reason: string | null;
  cost_usd: number;
  seq_head: number; // advisory event count of the active branch
  active_branch: string;
  branches: string[];
  created_at: number | null;
  updated_at: number | null;
  model: string | null;
  workspace: string | null;
  title: string | null; // model-written session title, when one has landed
  summary: string | null; // model-written one-liner of where the session stands
  created_by: string | null; // who started it, when recorded
  workflow: string | null; // the workflow whose task started it, if any
  run_id: string | null; // that workflow's run
  task: string | null; // the task id within the run
  trigger: string; // "api" | "console" | "schedule" | "manual"
  agent: string | null; // the stored agent its options were resolved from, if any
}

// Mirrors run_outcome() in src/syros/console/api.py — keep the two in sync.
// Liveness (SessionState) answers "is it going"; the outcome answers "how did
// it go", and the three live states carry through so one badge covers both.
export type RunOutcome =
  | "running"
  | "starting"
  | "queued"
  | "stalled"
  | "succeeded"
  | "failed"
  | "cancelled";

/** A session seen as one run of something. duration_s is null while it runs. */
export interface RunSummary extends SessionSummary {
  outcome: RunOutcome;
  duration_s: number | null;
}

/** One task in a workflow's definition — mirrors workflows.normalize_tasks. */
export interface WorkflowTaskSpec {
  id: string;
  prompt: string;
  agent: string | null; // stored agent (persona) the task runs as
  options: Record<string, unknown>; // per-task AgentOptions overrides
  depends_on: string[];
}

export type WorkflowTaskStatus =
  | "pending"
  | "launching"
  | "running"
  | "succeeded"
  | "failed"
  | "skipped";

/** One task's state within a run — mirrors ConsoleAPI._run_row. */
export interface WorkflowTaskState {
  status: WorkflowTaskStatus;
  session_id: string | null;
  error: string | null;
  started_at: number | null;
  finished_at: number | null;
  result_preview: string | null; // clipped; the full text lives in the session
}

export type WorkflowRunStatus = "running" | "succeeded" | "failed";

/** One firing of a workflow: orchestration state, task by task. */
export interface WorkflowRun {
  id: string;
  status: WorkflowRunStatus;
  trigger: string; // "schedule" | "manual"
  started_at: number | null;
  finished_at: number | null;
  spec: WorkflowTaskSpec[]; // the task list captured at launch
  tasks: Record<string, WorkflowTaskState>;
}

export interface WorkflowSummary {
  name: string;
  tasks: WorkflowTaskSpec[];
  options: Record<string, unknown>; // workflow-level option defaults
  cron: string | null; // null = manual-only
  timezone: string;
  enabled: boolean;
  next_run_at: number | null;
  last_run_at: number | null;
  last_skipped_at: number | null;
  last_error: string | null;
  run_count: number;
  skip_count: number;
  created_by: string | null;
  created_at: number | null;
  last_run: WorkflowRun | null;
}

export interface WorkflowsResponse {
  now: number;
  workflows: WorkflowSummary[];
}

export interface WorkflowResponse {
  now: number;
  workflow: WorkflowSummary;
  runs: WorkflowRun[];
}

/** A stored, named run configuration (persona) — agents/{name} in Firestore. */
export interface AgentSummary {
  name: string;
  description: string | null;
  options: Record<string, unknown>;
  created_by: string | null;
  created_at: number | null;
  updated_at: number | null;
}

export interface AgentsResponse {
  now: number;
  agents: AgentSummary[];
}

export interface AgentResponse {
  now: number;
  agent: AgentSummary;
}

export interface Approval {
  call_hash: string;
  tool_name: string;
  input: Record<string, unknown>;
  tool_use_id: string | null;
  requested_at: number;
  deadline: number;
}

export interface ApprovalWithSession extends Approval {
  session_id: string;
}

export interface ContentBlock {
  type: string;
  id?: string;
  text?: string;
  thinking?: string;
  name?: string;
  input?: unknown;
  tool_use_id?: string;
  content?: unknown;
  is_error?: boolean;
}

export interface TranscriptMessage {
  kind?: string;
  content?: string | ContentBlock[];
  subtype?: string;
  is_error?: boolean;
  num_turns?: number;
  total_cost_usd?: number | null;
  duration_ms?: number | null;
}

// One journal record — mirrors the envelope in src/syros/journal.py. The
// transcript is a tree: uuid/parent_uuid chain records within a branch, and
// only one branch (the session's active one) streams to the browser.
export type JournalType = "message" | "prompt" | "tool_call" | "approval" | "lifecycle";

export interface TranscriptEvent {
  uuid?: string;
  parent_uuid?: string | null;
  branch?: string;
  seq: number;
  type?: JournalType;
  ts?: number | null;
  context?: Record<string, unknown>;
  payload?: Record<string, unknown>;
}

/** The message a record renders as, or null for journal-only records
 *  (tool_call, approval, lifecycle) — mirrors journal.event_message. */
export function eventMessage(event: TranscriptEvent): TranscriptMessage | null {
  if (event.type === "message") return (event.payload || {}) as TranscriptMessage;
  if (event.type === "prompt")
    return { kind: "user", content: String((event.payload as { text?: string })?.text ?? "") };
  return null;
}

export interface SessionsResponse {
  now: number;
  sessions: SessionSummary[];
}

// POST /api/sessions/delete — best-effort, so it reports each session's fate
// instead of failing the whole request on the first one it can't remove.
export interface BulkDeleteResponse {
  ok: boolean;
  deleted: string[];
  failed: { id: string; error: string }[];
}

export interface PollResponse {
  now: number;
  session: SessionSummary;
  events: TranscriptEvent[];
  approvals: Approval[];
}

export interface ApprovalsResponse {
  now: number;
  approvals: ApprovalWithSession[];
}

export interface WorkspaceSessionRef {
  id: string;
  state: SessionState;
  updated_at: number | null;
}

export interface WorkspaceSummary {
  name: string;
  busy: boolean;
  lease_session_id: string | null;
  description: string | null;
  options: Record<string, unknown>; // serialized AgentOptions defaults
  file_count: number;
  total_size: number;
  updated: number | null;
  sessions: WorkspaceSessionRef[];
  members: string[]; // agents whose stored options name this workspace
}

export interface WorkspacesResponse {
  now: number;
  workspaces: WorkspaceSummary[];
}

export interface StoredFile {
  name: string;
  size: number;
  updated: number | null;
  tags?: string[];
}

export interface WorkspaceFilesResponse {
  now: number;
  name: string;
  files: StoredFile[];
}

/** Global option defaults (serialized AgentOptions) every workspace and session inherits. */
export interface SettingsResponse {
  now: number;
  options: Record<string, unknown>;
}

/** Reply shape of the file mutations (write, delete, rename, tags, folder). */
export interface OkResponse {
  now: number;
  ok: boolean;
  name?: string;
  file?: string;
  size?: number;
  count?: number;
  tags?: string[];
}

/** Reply shape of bulk file deletes — best-effort, per-file failures. */
export interface BulkFilesResponse {
  now: number;
  ok: boolean;
  deleted: string[];
  failed: { name: string; error: string }[];
}

export interface ConnectorSummary {
  name: string;
  label: string;
  auth: string;
  servers: string[];
  configured: boolean;
  updated: number | null;
}

export interface ConnectorsResponse {
  now: number;
  connectors: ConnectorSummary[];
}

export interface SkillSummary {
  name: string;
  file_count: number;
  total_size: number;
  updated: number | null;
  /** Workspaces that install this catalog skill. */
  installed_in: string[];
  /** On the global default (settings/global), i.e. installed everywhere a
   *  nearer layer doesn't install its own set. Separate from installed_in
   *  because "global" is also a legal workspace name. */
  installed_globally: boolean;
}

export interface SkillsResponse {
  now: number;
  skills: SkillSummary[];
}

export interface SkillFilesResponse {
  now: number;
  name: string;
  files: StoredFile[];
}

/** Reply shape of POST /api/skills/sync. */
export interface SyncSkillsResponse {
  now: number;
  ok: boolean;
  skills: string[];
  files: number;
  skipped: { skill: string; file: string; size: number }[];
}

export interface SpaceSummary {
  name: string;
  file_count: number;
  total_size: number;
  updated: number | null;
}

export interface SpacesResponse {
  now: number;
  spaces: SpaceSummary[];
}

export interface SpaceArtifactsResponse {
  now: number;
  space: string;
  artifacts: StoredFile[];
}
