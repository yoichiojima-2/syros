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
  seq_head: number;
  created_at: number | null;
  updated_at: number | null;
  model: string | null;
  workspace: string | null;
  schedule: string | null; // the schedule that started it, if any
  trigger: string; // "api" | "schedule" | "manual"
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

/** A session seen as one run of a schedule. duration_s is null while it runs. */
export interface RunSummary extends SessionSummary {
  outcome: RunOutcome;
  duration_s: number | null;
}

export interface ScheduleSummary {
  name: string;
  cron: string;
  timezone: string;
  prompt: string;
  options: Record<string, unknown>;
  enabled: boolean;
  next_run_at: number | null;
  last_run_at: number | null;
  last_skipped_at: number | null;
  last_error: string | null;
  runs: number;
  skips: number;
  created_by: string | null;
  created_at: number | null;
  last_run: RunSummary | null;
}

export interface SchedulesResponse {
  now: number;
  schedules: ScheduleSummary[];
}

export interface ScheduleResponse {
  now: number;
  schedule: ScheduleSummary;
  runs: RunSummary[];
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

export interface TranscriptEvent {
  seq: number;
  message: TranscriptMessage;
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
  file_count: number;
  total_size: number;
  updated: number | null;
  sessions: WorkspaceSessionRef[];
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
