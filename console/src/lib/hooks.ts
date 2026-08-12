"use client";

import { useEffect, useRef, useState } from "react";
import { api, serverNow, setOnlineListener } from "./api";
import type {
  Approval,
  ApprovalsResponse,
  ApprovalWithSession,
  PollResponse,
  SessionsResponse,
  SessionSummary,
  TranscriptEvent,
} from "./types";

/** Server-clock ticker for countdowns and relative times. */
export function useNow(intervalMs = 500): number {
  const [now, setNow] = useState(serverNow);
  useEffect(() => {
    const timer = setInterval(() => setNow(serverNow()), intervalMs);
    return () => clearInterval(timer);
  }, [intervalMs]);
  return now;
}

/** Connectivity flag fed by the fetch wrapper. One consumer (the app shell). */
export function useOnline(): boolean {
  const [online, setOnline] = useState(true);
  useEffect(() => {
    setOnlineListener(setOnline);
    return () => setOnlineListener(null);
  }, []);
  return online;
}

function usePolling(poll: () => void, intervalMs: number) {
  useEffect(() => {
    const tick = () => {
      if (!document.hidden) poll();
    };
    tick();
    const timer = setInterval(tick, intervalMs);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intervalMs]);
}

export function useSessions(intervalMs = 4000): SessionSummary[] | null {
  const [sessions, setSessions] = useState<SessionSummary[] | null>(null);
  usePolling(() => {
    api<SessionsResponse>("/api/sessions")
      .then((data) => setSessions(data.sessions))
      .catch(() => {});
  }, intervalMs);
  return sessions;
}

export function useApprovals(intervalMs = 3000): {
  approvals: ApprovalWithSession[] | null;
  remove: (callHash: string) => void;
} {
  const [approvals, setApprovals] = useState<ApprovalWithSession[] | null>(null);
  usePolling(() => {
    api<ApprovalsResponse>("/api/approvals")
      .then((data) =>
        setApprovals((prev) =>
          prev && sameApprovals(prev, data.approvals) ? prev : data.approvals,
        ),
      )
      .catch(() => {});
  }, intervalMs);
  const remove = (callHash: string) =>
    setApprovals((prev) => prev?.filter((a) => a.call_hash !== callHash) ?? prev);
  return { approvals, remove };
}

/** Identity compare so countdown cards aren't re-created every poll. */
function sameApprovals(a: Approval[], b: Approval[]): boolean {
  return a.map((x) => x.call_hash).join(",") === b.map((x) => x.call_hash).join(",");
}

export interface SessionPoll {
  session: SessionSummary | null;
  events: TranscriptEvent[];
  approvals: Approval[];
  removeApproval: (callHash: string) => void;
}

/** 1s poll of the focused session: append-only event cursor + approvals. */
export function useSessionPoll(sid: string | null): SessionPoll {
  const [session, setSession] = useState<SessionSummary | null>(null);
  const [events, setEvents] = useState<TranscriptEvent[]>([]);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const cursorRef = useRef(0);

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
        const data = await api<PollResponse>(`/api/sessions/${sid}/poll?after=${cursorRef.current}`);
        if (cancelled) return;
        setSession(data.session);
        if (data.events.length) {
          cursorRef.current = data.events[data.events.length - 1].seq;
          setEvents((prev) => [...prev, ...data.events]);
        }
        setApprovals((prev) => (sameApprovals(prev, data.approvals) ? prev : data.approvals));
      } catch {
        // connectivity is surfaced by the shell indicator
      }
    };
    poll();
    const timer = setInterval(poll, 1000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [sid]);

  const removeApproval = (callHash: string) =>
    setApprovals((prev) => prev.filter((a) => a.call_hash !== callHash));

  return { session, events, approvals, removeApproval };
}

/** Transient status line; clears itself after 4s. */
export function useFlash(): [string, (text: string) => void] {
  const [flash, setFlash] = useState("");
  const show = (text: string) => {
    setFlash(text);
    if (text) setTimeout(() => setFlash((cur) => (cur === text ? "" : cur)), 4000);
  };
  return [flash, show];
}
