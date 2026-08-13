// Presentation helpers shared across the console components.

export function relTime(epoch: number | null, now: number): string {
  if (!epoch) return "";
  const s = Math.max(0, now - epoch);
  if (s < 60) return `${Math.floor(s)}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

/** Time until a future instant: "in 4m", "due now", "12m overdue". */
export function untilTime(epoch: number | null, now: number): string {
  if (!epoch) return "—";
  const s = epoch - now;
  if (s <= 0) return s > -60 ? "due now" : `${duration(-s)} overdue`;
  return `in ${duration(s)}`;
}

/** Coarse wall-clock spans: one unit below a minute, two above. */
export function duration(seconds: number | null): string {
  if (seconds === null || !isFinite(seconds)) return "—";
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
  return `${Math.floor(s / 3600)}h ${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}m`;
}

/** Absolute local time, for the columns where "3h ago" isn't enough. */
export function clockTime(epoch: number | null): string {
  if (!epoch) return "—";
  const date = new Date(epoch * 1000);
  const time = date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const today = new Date().toDateString() === date.toDateString();
  return today ? time : `${date.getMonth() + 1}/${date.getDate()} ${time}`;
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

export function bytes(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

export function pretty(value: unknown): string {
  return typeof value === "string" ? value : JSON.stringify(value, null, 2);
}

