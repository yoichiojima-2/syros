// Presentation helpers shared across the console components.

export function relTime(epoch: number | null, now: number): string {
  if (!epoch) return "";
  const s = Math.max(0, now - epoch);
  if (s < 60) return `${Math.floor(s)}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export function shortId(sid: string): string {
  return sid.length > 16 ? sid.slice(0, 13) + "…" : sid;
}

export function cost(value: number | null | undefined): string {
  return `$${(value || 0).toFixed(4)}`;
}

export function compact(value: unknown, max: number): string {
  const text = typeof value === "string" ? value : (JSON.stringify(value) ?? "");
  return text.length > max ? text.slice(0, max) + "…" : text;
}

export function pretty(value: unknown): string {
  return typeof value === "string" ? value : JSON.stringify(value, null, 2);
}

export const ACTIVE_STATES = new Set(["running", "queued", "stalled"]);
