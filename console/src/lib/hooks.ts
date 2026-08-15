"use client";

import { useEffect, useRef, useState } from "react";
import { api, serverNow, setOnlineListener } from "./api";
import type {
  AgentResponse,
  AgentsResponse,
  AgentSummary,
  Approval,
  ApprovalsResponse,
  ApprovalWithSession,
  ConnectorsResponse,
  ConnectorSummary,
  PollResponse,
  WorkflowResponse,
  WorkflowRun,
  WorkflowSummary,
  WorkflowsResponse,
  SessionsResponse,
  SessionSummary,
  SkillFilesResponse,
  SkillsResponse,
  SkillSummary,
  SpaceArtifactsResponse,
  SpaceSummary,
  SpacesResponse,
  StoredFile,
  WorkspaceFilesResponse,
  WorkspacesResponse,
  WorkspaceSummary,
  TranscriptEvent,
} from "./types";

/** Ticker on the server's clock (see api.ts) driving countdowns and relative times. */
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

// `deps` re-runs the effect when they change — a refresh nonce to pull fresh
// data without waiting out the interval, or the name the poll closes over.
// The poll gets an `alive` check so a response landing after the deps moved on
// (or the component unmounted) can be dropped instead of applied.
function usePolling(
  poll: (alive: () => boolean) => void,
  intervalMs: number,
  deps: readonly unknown[] = [],
) {
  useEffect(() => {
    let alive = true;
    const tick = () => {
      if (!document.hidden) poll(() => alive);
    };
    tick();
    const timer = setInterval(tick, intervalMs);
    return () => {
      alive = false;
      clearInterval(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intervalMs, ...deps]);
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

export function useWorkflows(intervalMs = 5000): {
  workflows: WorkflowSummary[] | null;
  refresh: () => void;
} {
  const [workflows, setWorkflows] = useState<WorkflowSummary[] | null>(null);
  const [nonce, setNonce] = useState(0);
  usePolling(
    () => {
      api<WorkflowsResponse>("/api/workflows")
        .then((data) => setWorkflows(data.workflows))
        .catch(() => {});
    },
    intervalMs,
    [nonce],
  );
  return { workflows, refresh: () => setNonce((n) => n + 1) };
}

/** One workflow and its run history — the run-status view's feed. */
export function useWorkflow(
  name: string | null,
  intervalMs = 4000,
): {
  workflow: WorkflowSummary | null;
  runs: WorkflowRun[] | null;
  missing: boolean;
  refresh: () => void;
} {
  const [data, setData] = useState<WorkflowResponse | null>(null);
  const [missing, setMissing] = useState(false);
  const [nonce, setNonce] = useState(0);
  useEffect(() => {
    setData(null);
    setMissing(false);
    if (!name) return;
    let cancelled = false;
    const poll = () => {
      if (document.hidden) return;
      api<WorkflowResponse>(`/api/workflows/${encodeURIComponent(name)}`)
        .then((next) => {
          if (cancelled) return;
          setData(next);
          setMissing(false);
        })
        // A deleted workflow 404s; say so rather than spinning on a skeleton.
        .catch((err: Error) => {
          if (!cancelled && /not found/i.test(err.message)) setMissing(true);
        });
    };
    poll();
    const timer = setInterval(poll, intervalMs);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [name, intervalMs, nonce]);
  return {
    workflow: data?.workflow ?? null,
    runs: data?.runs ?? null,
    missing,
    refresh: () => setNonce((n) => n + 1),
  };
}

export function useAgents(intervalMs = 5000): {
  agents: AgentSummary[] | null;
  refresh: () => void;
} {
  const [agents, setAgents] = useState<AgentSummary[] | null>(null);
  const [nonce, setNonce] = useState(0);
  usePolling(
    () => {
      api<AgentsResponse>("/api/agents")
        .then((data) => setAgents(data.agents))
        .catch(() => {});
    },
    intervalMs,
    [nonce],
  );
  return { agents, refresh: () => setNonce((n) => n + 1) };
}

/** One stored agent — the agent detail view's feed. */
export function useAgent(
  name: string | null,
  intervalMs = 5000,
): {
  agent: AgentSummary | null;
  missing: boolean;
  refresh: () => void;
} {
  const [agent, setAgent] = useState<AgentSummary | null>(null);
  const [missing, setMissing] = useState(false);
  const [nonce, setNonce] = useState(0);
  useEffect(() => {
    setAgent(null);
    setMissing(false);
    if (!name) return;
    let cancelled = false;
    const poll = () => {
      if (document.hidden) return;
      api<AgentResponse>(`/api/agents/${encodeURIComponent(name)}`)
        .then((next) => {
          if (cancelled) return;
          setAgent(next.agent);
          setMissing(false);
        })
        // a deleted agent 404s; say so rather than spinning on a skeleton
        .catch((err: Error) => {
          if (!cancelled && /not found/i.test(err.message)) setMissing(true);
        });
    };
    poll();
    const timer = setInterval(poll, intervalMs);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [name, intervalMs, nonce]);
  return { agent, missing, refresh: () => setNonce((n) => n + 1) };
}

export function useWorkspaces(intervalMs = 4000): WorkspaceSummary[] | null {
  const [workspaces, setWorkspaces] = useState<WorkspaceSummary[] | null>(null);
  usePolling(() => {
    api<WorkspacesResponse>("/api/workspaces")
      .then((data) => setWorkspaces(data.workspaces))
      .catch(() => {});
  }, intervalMs);
  return workspaces;
}

/** Files in one workspace's shared directory. Unlike the artifact equivalent this exposes
 *  a refresh, so a save or delete shows up without waiting out the poll. */
export function useWorkspaceFiles(
  name: string | null,
  intervalMs = 8000,
): { files: StoredFile[] | null; refresh: () => void } {
  const [files, setFiles] = useState<StoredFile[] | null>(null);
  const [nonce, setNonce] = useState(0);
  // reset only when the workspace changes — a refresh must not flash a skeleton
  useEffect(() => setFiles(null), [name]);
  usePolling(
    (alive) => {
      if (!name) return;
      api<WorkspaceFilesResponse>(`/api/workspaces/${encodeURIComponent(name)}/files`)
        .then((data) => {
          if (alive()) setFiles(data.files);
        })
        // an unknown workspace 404s; keep whatever we already had otherwise
        .catch(() => {
          if (alive()) setFiles((prev) => prev ?? []);
        });
    },
    intervalMs,
    [name, nonce],
  );
  return { files, refresh: () => setNonce((n) => n + 1) };
}

/** Platform connectors: catalog + credential status. Secret metadata changes
 *  rarely, so a slow poll is plenty. */
export function useConnectors(intervalMs = 15000): ConnectorSummary[] | null {
  const [connectors, setConnectors] = useState<ConnectorSummary[] | null>(null);
  usePolling(() => {
    api<ConnectorsResponse>("/api/connectors")
      .then((data) => setConnectors(data.connectors))
      .catch(() => {});
  }, intervalMs);
  return connectors;
}

/** Skills in one scope: global (workspace null) or a workspace's own skill set.
 *  Exposes a refresh like useSkillFiles, so an upload shows up without waiting
 *  out the poll. */
export function useSkills(
  workspace: string | null = null,
  intervalMs = 8000,
): { skills: SkillSummary[] | null; refresh: () => void } {
  const [skills, setSkills] = useState<SkillSummary[] | null>(null);
  const [nonce, setNonce] = useState(0);
  // reset only when the scope changes — a refresh must not flash a skeleton
  useEffect(() => setSkills(null), [workspace]);
  usePolling(
    (alive) => {
      api<SkillsResponse>(`/api/skills${workspace ? `?workspace=${encodeURIComponent(workspace)}` : ""}`)
        .then((data) => {
          if (alive()) setSkills(data.skills);
        })
        .catch(() => {});
    },
    intervalMs,
    [workspace, nonce],
  );
  return { skills, refresh: () => setNonce((n) => n + 1) };
}

/** Files in one skill (global or workspace-scoped). Like useWorkspaceFiles this exposes
 *  a refresh, so a save or delete shows up without waiting out the poll. */
export function useSkillFiles(
  name: string | null,
  workspace: string | null = null,
  intervalMs = 8000,
): { files: StoredFile[] | null; refresh: () => void } {
  const [files, setFiles] = useState<StoredFile[] | null>(null);
  const [nonce, setNonce] = useState(0);
  // reset only when the skill changes — a refresh must not flash a skeleton
  useEffect(() => setFiles(null), [name, workspace]);
  usePolling(
    (alive) => {
      if (!name) return;
      const query = workspace ? `?workspace=${encodeURIComponent(workspace)}` : "";
      api<SkillFilesResponse>(`/api/skills/${encodeURIComponent(name)}/files${query}`)
        .then((data) => {
          if (alive()) setFiles(data.files);
        })
        // an unknown skill 404s; keep whatever we already had otherwise
        .catch(() => {
          if (alive()) setFiles((prev) => prev ?? []);
        });
    },
    intervalMs,
    [name, workspace, nonce],
  );
  return { files, refresh: () => setNonce((n) => n + 1) };
}

export function useArtifactSpaces(intervalMs = 8000): SpaceSummary[] | null {
  const [spaces, setSpaces] = useState<SpaceSummary[] | null>(null);
  usePolling(() => {
    api<SpacesResponse>("/api/artifacts")
      .then((data) => setSpaces(data.spaces))
      .catch(() => {});
  }, intervalMs);
  return spaces;
}

export function useSpaceArtifacts(
  space: string | null,
  intervalMs = 8000,
): { files: StoredFile[] | null; refresh: () => void } {
  const [files, setFiles] = useState<StoredFile[] | null>(null);
  const [nonce, setNonce] = useState(0);
  // reset only when the space changes — a refresh must not flash a skeleton
  useEffect(() => setFiles(null), [space]);
  useEffect(() => {
    if (!space) return;
    let cancelled = false;
    const poll = () => {
      if (document.hidden) return;
      api<SpaceArtifactsResponse>(`/api/artifacts/${space}`)
        .then((data) => {
          if (!cancelled) setFiles(data.artifacts);
        })
        .catch(() => {});
    };
    poll();
    const timer = setInterval(poll, intervalMs);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [space, intervalMs, nonce]);
  return { files, refresh: () => setNonce((n) => n + 1) };
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

// Compare by call_hash so a poll keeps the previous array (and skips a
// re-render of the countdown cards) when the pending set hasn't changed.
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
  const branchRef = useRef<string | null>(null);

  useEffect(() => {
    setSession(null);
    setEvents([]);
    setApprovals([]);
    cursorRef.current = 0;
    branchRef.current = null;
    if (!sid) return;
    let cancelled = false;
    const poll = async () => {
      if (document.hidden) return;
      try {
        const data = await api<PollResponse>(`/api/sessions/${sid}/poll?after=${cursorRef.current}`);
        if (cancelled) return;
        setSession(data.session);
        // A rewind switches the session's active branch: restart the cursor
        // so the transcript re-fetches from the branch base instead of
        // gluing two branches together.
        const branch = data.session.active_branch ?? "main";
        if (branchRef.current !== null && branchRef.current !== branch) {
          branchRef.current = branch;
          cursorRef.current = 0;
          setEvents([]);
          return;
        }
        branchRef.current = branch;
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

/** useFlash plus the try/catch every action shares: flash the message the
 * action returns on success, flash the error on failure. */
export function useAction(): [string, (fn: () => Promise<string | void>) => Promise<void>] {
  const [flash, showFlash] = useFlash();
  const run = async (fn: () => Promise<string | void>) => {
    try {
      const message = await fn();
      if (message) showFlash(message);
    } catch (err) {
      showFlash(`error: ${(err as Error).message}`);
    }
  };
  return [flash, run];
}
