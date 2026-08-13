// Artifacts, reconstructed client-side from the transcript. Every Write/Edit
// tool call carries the full file content (or a replayable patch) in its
// input, and the tool_result confirms it landed — so the console can show
// workspace files without a workspace API, and the store stays the only
// backend surface. Files that existed before the transcript began can't be
// replayed and simply don't appear.

import type { ContentBlock, TranscriptEvent } from "./types";

export type PreviewKind = "html" | "markdown" | "svg" | "code";

export interface ArtifactVersion {
  seq: number; // seq of the event carrying the tool_use
  order: number; // global write order; seq ties when one message writes twice
  op: "write" | "edit";
  content: string;
}

export interface Artifact {
  path: string;
  name: string; // basename, for chips and the panel header
  ext: string;
  kind: PreviewKind;
  versions: ArtifactVersion[]; // oldest first; last is current
  // An edit we couldn't replay (its old_string was missing) means the real
  // file has diverged from what we're showing — something outside Write/Edit
  // touched it. The panel says so rather than presenting stale content as
  // authoritative.
  stale: boolean;
}

const EDIT_TOOLS = new Set(["Write", "Edit", "MultiEdit"]);

/** The workspace path a tool_use block writes to, or null if it isn't a file edit. */
export function artifactToolPath(block: ContentBlock): string | null {
  if (block.type !== "tool_use" || !EDIT_TOOLS.has(block.name || "")) return null;
  const path = (block.input as Record<string, unknown> | undefined)?.file_path;
  return typeof path === "string" && path ? path : null;
}

export function previewKind(path: string): PreviewKind {
  const ext = extension(path);
  if (ext === "html" || ext === "htm") return "html";
  if (ext === "md" || ext === "markdown") return "markdown";
  if (ext === "svg") return "svg";
  return "code";
}

function extension(path: string): string {
  const name = path.split("/").pop() || "";
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(dot + 1).toLowerCase() : "";
}

interface EditSpec {
  old_string?: unknown;
  new_string?: unknown;
  replace_all?: unknown;
}

/** Literal (non-regex) replacement. String.replace would expand $&, $`, $$ in
 * the replacement, silently corrupting any file whose edit inserts a `$`. */
function applyEdit(content: string, edit: EditSpec): string | null {
  const oldStr = typeof edit.old_string === "string" ? edit.old_string : "";
  const newStr = typeof edit.new_string === "string" ? edit.new_string : "";
  if (!oldStr) return null;
  if (edit.replace_all) {
    if (!content.includes(oldStr)) return null;
    return content.split(oldStr).join(newStr);
  }
  const at = content.indexOf(oldStr);
  if (at < 0) return null;
  return content.slice(0, at) + newStr + content.slice(at + oldStr.length);
}

interface FileState {
  versions: ArtifactVersion[];
  stale: boolean;
}

/** Replay confirmed Write/Edit calls into per-file version histories,
 * most recently written file first. */
export function deriveArtifacts(events: TranscriptEvent[]): Artifact[] {
  const pending = new Map<string, { seq: number; tool: string; input: Record<string, unknown> }>();
  const files = new Map<string, FileState>();
  let order = 0;

  for (const event of events) {
    const message = event.message || {};
    if (!Array.isArray(message.content)) continue;
    for (const block of message.content) {
      if (message.kind === "assistant" && block.type === "tool_use") {
        if (block.id && EDIT_TOOLS.has(block.name || ""))
          pending.set(block.id, {
            seq: event.seq,
            tool: block.name || "",
            input: (block.input || {}) as Record<string, unknown>,
          });
        continue;
      }
      if (message.kind !== "user" || block.type !== "tool_result" || !block.tool_use_id) continue;
      const call = pending.get(block.tool_use_id);
      if (!call) continue;
      pending.delete(block.tool_use_id);
      // Denied or failed calls never touched the workspace.
      if (block.is_error) continue;
      const path = typeof call.input.file_path === "string" ? call.input.file_path : "";
      if (!path) continue;
      const state = files.get(path) || { versions: [], stale: false };
      let next: string | null;
      let op: ArtifactVersion["op"];
      if (call.tool === "Write") {
        next = typeof call.input.content === "string" ? call.input.content : null;
        op = "write";
      } else {
        // An edit only replays on top of content we've seen written.
        next = state.versions.length ? state.versions[state.versions.length - 1].content : null;
        op = "edit";
        const edits =
          call.tool === "Edit"
            ? [call.input as EditSpec]
            : Array.isArray(call.input.edits)
              ? (call.input.edits as EditSpec[])
              : [];
        if (!edits.length) next = null;
        for (const edit of edits) next = next === null ? null : applyEdit(next, edit);
      }
      if (next === null) {
        // The write landed in the workspace but we can't reproduce it, so what
        // we hold is no longer the file. Only meaningful once we have content.
        if (state.versions.length) {
          state.stale = true;
          files.set(path, state);
        }
        continue;
      }
      state.versions.push({ seq: call.seq, order: order++, op, content: next });
      files.set(path, state);
    }
  }

  return [...files.entries()]
    .filter(([, state]) => state.versions.length > 0)
    .map(([path, state]) => ({
      path,
      name: path.split("/").pop() || path,
      ext: extension(path),
      kind: previewKind(path),
      versions: state.versions,
      stale: state.stale,
    }))
    .sort(
      (a, b) =>
        b.versions[b.versions.length - 1].order - a.versions[a.versions.length - 1].order,
    );
}

/** Disambiguating label: bare basename unless another artifact shares it, in
 * which case enough trailing path segments to tell them apart. */
export function artifactLabel(artifact: Artifact, all: Artifact[]): string {
  const clashes = all.filter((a) => a !== artifact && a.name === artifact.name);
  if (!clashes.length) return artifact.name;
  const segments = artifact.path.split("/").filter(Boolean);
  return segments.slice(-2).join("/") || artifact.path;
}
