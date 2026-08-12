// Shapes of the JSON payloads served by src/syros/console/api.py.
// All timestamps are epoch seconds (the server flattens Firestore datetimes),
// and the message/block shapes mirror syros.types' Firestore serialization.

export interface SessionSummary {
  id: string;
  status: string | null;
  state: string; // derived liveness: running | stalled | queued | idle | terminated | ...
  disabled: boolean;
  stop_reason: string | null;
  cost_usd: number;
  seq_head: number;
  created_at: number | null;
  updated_at: number | null;
  model: string | null;
}

export interface Approval {
  call_hash: string;
  tool_name: string;
  input: Record<string, unknown>;
  tool_use_id: string | null;
  requested_at: number;
  deadline: number;
}

export interface ContentBlock {
  type: string;
  text?: string;
  thinking?: string;
  name?: string;
  input?: unknown;
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
