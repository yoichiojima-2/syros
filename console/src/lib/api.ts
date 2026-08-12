// Thin fetch wrapper: tracks connectivity and the server clock offset so
// countdowns render skew-proof (every response carries "now").

let clockOffset = 0; // serverNow - localNow, seconds
let onlineListener: ((online: boolean) => void) | null = null;

export function setOnlineListener(listener: ((online: boolean) => void) | null): void {
  onlineListener = listener;
}

export function serverNow(): number {
  return Date.now() / 1000 + clockOffset;
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, init);
  } catch (err) {
    onlineListener?.(false);
    throw err;
  }
  onlineListener?.(true);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || String(response.status));
  if (data.now) clockOffset = data.now - Date.now() / 1000;
  return data as T;
}

export function post<T>(path: string, body?: unknown): Promise<T> {
  return api<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}
