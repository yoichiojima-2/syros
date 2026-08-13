// Shapes of the JSON payloads served by src/syros/console/api.py.
// All timestamps are epoch seconds (the server flattens Firestore datetimes),
// and the message/block shapes mirror syros.types' Firestore serialization.

// Mirrors derived_state() in src/syros/console/api.py — keep the two in sync.
export type SessionState = "running" | "stalled" | "queued" | "idle" | "terminated" | "unknown";

export const ACTIVE_STATES: ReadonlySet<SessionState> = new Set([
  "running",
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
}

export interface WorkspaceFilesResponse {
  now: number;
  name: string;
  files: StoredFile[];
}

/** Reply shape of the workspace file mutations (write, delete). */
export interface OkResponse {
  now: number;
  ok: boolean;
  name: string;
  file: string;
  size?: number;
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
