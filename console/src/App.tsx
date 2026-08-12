import { useCallback, useEffect, useRef, useState } from "react";
import { api, post, serverNow, setOnlineListener } from "./api";
import { cost, stateDotClass } from "./format";
import type {
  Approval,
  PollResponse,
  SessionsResponse,
  SessionSummary,
  TranscriptEvent,
} from "./types";
import ApprovalCard from "./components/ApprovalCard";
import Composer from "./components/Composer";
import Sidebar from "./components/Sidebar";
import Transcript from "./components/Transcript";

// Compare by call_hash so the 1s poll keeps the previous array (and skips a
// re-render) when the pending set hasn't actually changed.
function sameApprovals(a: Approval[], b: Approval[]): boolean {
  return (
    a.map((x) => x.call_hash).join(",") === b.map((x) => x.call_hash).join(",")
  );
}

// The focused session id lives in the URL hash, so sessions are deep-linkable
// and the back button walks between them.
function hashSid(): string | null {
  return location.hash.length > 1 ? location.hash.slice(1) : null;
}

export default function App() {
  const [sessions, setSessions] = useState<SessionSummary[] | null>(null);
  const [sid, setSid] = useState<string | null>(hashSid);
  const [session, setSession] = useState<SessionSummary | null>(null);
  const [events, setEvents] = useState<TranscriptEvent[]>([]);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [online, setOnline] = useState(true);
  const [flash, setFlash] = useState("");
  const [now, setNow] = useState(serverNow);
  const cursorRef = useRef(0);

  useEffect(() => {
    setOnlineListener(setOnline);
    // 500ms clock tick (on the server's clock) drives the approval countdowns.
    const tick = setInterval(() => setNow(serverNow()), 500);
    const onHash = () => setSid(hashSid());
    window.addEventListener("hashchange", onHash);
    return () => {
      clearInterval(tick);
      window.removeEventListener("hashchange", onHash);
    };
  }, []);

  const showFlash = useCallback((text: string) => {
    setFlash(text);
    if (text) setTimeout(() => setFlash((cur) => (cur === text ? "" : cur)), 4000);
  }, []);

  // session list: refresh every 4s while the tab is visible
  useEffect(() => {
    const refresh = () => {
      if (document.hidden) return;
      api<SessionsResponse>("/api/sessions")
        .then((data) => setSessions(data.sessions))
        .catch(() => {});
    };
    refresh();
    const timer = setInterval(refresh, 4000);
    return () => clearInterval(timer);
  }, []);

  // focused session: poll events + approvals every 1s
  useEffect(() => {
    setSession(null);
    setEvents([]);
    setApprovals([]);
    cursorRef.current = 0;
    if (!sid) return;
    let cancelled = false;
    const poll = async () => {
      if (document.hidden) return;
      try {
        const data = await api<PollResponse>(
          `/api/sessions/${sid}/poll?after=${cursorRef.current}`,
        );
        if (cancelled) return;
        setSession(data.session);
        if (data.events.length) {
          cursorRef.current = data.events[data.events.length - 1].seq;
          setEvents((prev) => [...prev, ...data.events]);
        }
        setApprovals((prev) => (sameApprovals(prev, data.approvals) ? prev : data.approvals));
      } catch {
        // connectivity is surfaced by the header indicator
      }
    };
    poll();
    const timer = setInterval(poll, 1000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [sid]);

  const decide = async (callHash: string, allow: boolean, message: string | null) => {
    try {
      await post(`/api/sessions/${sid}/approvals/${callHash}`, { allow, message });
      // Drop the card immediately; a decided approval leaves the pending set
      // server-side, so the next poll agrees.
      setApprovals((prev) => prev.filter((a) => a.call_hash !== callHash));
      showFlash(allow ? "allowed" : "denied");
    } catch (err) {
      showFlash(`error: ${(err as Error).message}`);
    }
  };

  const sendPrompt = async (text: string) => {
    try {
      const result = await post<{ triggered: boolean }>(`/api/sessions/${sid}/prompt`, { text });
      showFlash(result.triggered ? "queued · runner starting…" : "queued");
    } catch (err) {
      showFlash(`error: ${(err as Error).message}`);
    }
  };

  const interrupt = async () => {
    try {
      await post(`/api/sessions/${sid}/interrupt`);
      showFlash("interrupt queued");
    } catch (err) {
      showFlash(`error: ${(err as Error).message}`);
    }
  };

  const kill = async () => {
    if (!confirm(`Kill ${sid}? The session terminates and cannot be resumed.`)) return;
    try {
      await post(`/api/sessions/${sid}/kill`);
      showFlash("killed");
    } catch (err) {
      showFlash(`error: ${(err as Error).message}`);
    }
  };

  const dead = session?.state === "terminated";

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center gap-3 border-b border-line bg-panel px-4 py-2.5">
        <span className="font-mono text-[15px] font-semibold tracking-tight">syros</span>
        <span className="font-mono text-xs text-dim">{location.host}</span>
        <span className="flex-1" />
        <span className="flex items-center gap-1.5 font-mono text-[11px] text-dim">
          <span className={`size-[7px] rounded-full ${online ? "bg-ok" : "bg-danger"}`} />
          {online ? "connected" : "offline"}
        </span>
      </header>

      <main className="grid min-h-0 flex-1 grid-cols-[264px_1fr] grid-rows-[minmax(0,1fr)] max-md:grid-cols-1 max-md:grid-rows-[minmax(0,30vh)_minmax(0,1fr)]">
        <Sidebar
          sessions={sessions}
          activeId={sid}
          now={now}
          onSelect={(id) => {
            location.hash = id;
          }}
        />

        <section className="flex min-h-0 min-w-0 flex-col">
          {session && (
            <div className="flex flex-wrap items-center gap-2.5 border-b border-line px-5 py-2.5">
              <span className="font-mono text-[13px] font-semibold">{session.id}</span>
              <span className="inline-flex items-center gap-1.5 rounded-full border border-line px-2.5 py-0.5 font-mono text-[11px] text-dim">
                <span className={`size-[7px] rounded-full ${stateDotClass(session.state)}`} />
                {session.state}
              </span>
              <span className="font-mono text-[11px] text-dim">
                {session.model || ""} {cost(session.cost_usd)}
                {session.stop_reason ? ` · ${session.stop_reason}` : ""}
              </span>
              <span className="flex-1" />
              <button
                type="button"
                onClick={interrupt}
                disabled={dead}
                className="rounded-md border border-line bg-raised px-3 py-1 text-[13px] transition-colors hover:border-dim disabled:opacity-40"
              >
                Interrupt
              </button>
              <button
                type="button"
                onClick={kill}
                disabled={dead}
                className="rounded-md border border-line bg-raised px-3 py-1 text-[13px] transition-colors hover:border-danger hover:text-danger disabled:opacity-40"
              >
                Kill
              </button>
            </div>
          )}

          <Transcript
            events={events}
            placeholder={
              sid
                ? session
                  ? "No messages yet."
                  : "loading…"
                : 'Select a session — or run a query with sandbox="gcp" and it will appear on the left.'
            }
          />

          {approvals.length > 0 && (
            <div className="mx-auto w-full max-w-3xl space-y-2.5 px-5">
              {approvals.map((approval) => (
                <ApprovalCard
                  key={approval.call_hash}
                  approval={approval}
                  now={now}
                  onDecide={decide}
                />
              ))}
            </div>
          )}

          {flash && (
            <p className="pt-1.5 text-center font-mono text-[11px] text-dim">{flash}</p>
          )}

          {sid && <Composer disabled={dead} onSend={sendPrompt} />}
        </section>
      </main>
    </div>
  );
}
