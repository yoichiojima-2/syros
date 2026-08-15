"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Bot, CalendarClock, CircleStop, OctagonX, PanelRight, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { StateBadge } from "@/components/state-badge";
import { Transcript } from "@/components/transcript";
import { Composer } from "@/components/composer";
import { ApprovalCard } from "@/components/approval-card";
import { ArtifactPanel } from "@/components/artifact-panel";
import { deriveArtifacts } from "@/lib/artifacts";
import { eventMessage } from "@/lib/types";
import { useAction, useNow, useSessionPoll } from "@/lib/hooks";
import { post } from "@/lib/api";
import { cost, shortId } from "@/lib/format";

export function SessionDetail({ sid }: { sid: string }) {
  const router = useRouter();
  const { session, events, approvals, removeApproval } = useSessionPoll(sid);
  const now = useNow();
  const [flash, run] = useAction();
  const dead = session?.state === "terminated";

  // Prompts echo in the transcript the moment they're sent; each dimmed copy
  // drops when the runner mirrors the real event back into the feed.
  const [pending, setPending] = useState<string[]>([]);
  const scannedRef = useRef(0);
  useEffect(() => {
    const fresh = events.slice(scannedRef.current);
    scannedRef.current = events.length;
    if (!fresh.length) return;
    setPending((prev) => {
      let next = prev;
      for (const event of fresh) {
        const message = eventMessage(event);
        if (message?.kind !== "user" || typeof message.content !== "string") continue;
        const i = next.indexOf(message.content);
        if (i !== -1) next = [...next.slice(0, i), ...next.slice(i + 1)];
      }
      return next;
    });
  }, [events]);

  // The agent owes a response while a prompt waits in the inbox or a live
  // turn hasn't reached its result row yet — that's when the typing dots show.
  // Last *message* kind: journal-only records (audit rows, lifecycle) at the
  // tail must not hide a turn's result from the typing indicator.
  const lastKind = events.reduce<string | undefined>(
    (kind, event) => eventMessage(event)?.kind ?? kind,
    undefined,
  );
  const working =
    !dead &&
    (pending.length > 0 ||
      ((session?.state === "running" ||
        session?.state === "starting" ||
        session?.state === "queued") &&
        lastKind !== "result"));

  // Artifacts replay from the transcript (see lib/artifacts.ts). The panel
  // opens itself when a version lands and stays closed once dismissed, until
  // the next write — mirroring how claude.ai surfaces artifacts.
  const artifacts = useMemo(() => deriveArtifacts(events), [events]);
  const artifactPaths = useMemo(() => new Set(artifacts.map((a) => a.path)), [artifacts]);
  const [panelOpen, setPanelOpen] = useState(false);
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const versionCount = artifacts.reduce((n, a) => n + a.versions.length, 0);
  const seenVersionsRef = useRef(0);
  useEffect(() => {
    if (versionCount <= seenVersionsRef.current) return;
    seenVersionsRef.current = versionCount;
    // Don't yank a reader off the artifact they chose: follow the newest write
    // only when the panel isn't already showing something.
    if (!panelOpen) {
      setSelectedPath(artifacts[0].path);
      // Below lg the panel takes the column over, so only volunteer it where
      // it sits beside the transcript; a narrow viewport opens on request.
      if (window.matchMedia("(min-width: 1024px)").matches) setPanelOpen(true);
    }
  }, [versionCount, artifacts, panelOpen]);
  useEffect(() => {
    // new sid: the poll hook resets events, so reset the artifact state too
    seenVersionsRef.current = 0;
    setPanelOpen(false);
    setSelectedPath(null);
    setPending([]);
    scannedRef.current = 0;
  }, [sid]);

  const openArtifact = (path: string) => {
    if (artifacts.some((a) => a.path === path)) setSelectedPath(path);
    setPanelOpen(true);
  };

  const decide = (callHash: string, allow: boolean, message: string | null) =>
    run(async () => {
      await post(`/api/sessions/${sid}/approvals/${callHash}`, { allow, message });
      // Drop the card immediately; a decided approval leaves the pending set
      // server-side, so the next poll agrees.
      removeApproval(callHash);
      return allow ? "allowed" : "denied";
    });

  const sendPrompt = async (text: string) => {
    let failed = false;
    await run(async () => {
      setPending((prev) => [...prev, text]);
      try {
        const result = await post<{ triggered: boolean }>(`/api/sessions/${sid}/prompt`, { text });
        return result.triggered ? "queued · runner starting…" : "queued";
      } catch (err) {
        // the POST never landed, so the optimistic bubble comes back out
        setPending((prev) => {
          const i = prev.lastIndexOf(text);
          return i === -1 ? prev : [...prev.slice(0, i), ...prev.slice(i + 1)];
        });
        failed = true;
        throw err;
      }
    });
    // run() turns the failure into a flash and resolves; rejecting here is what
    // hands the typed prompt back to the composer instead of eating it.
    if (failed) throw new Error("prompt not sent");
  };

  const interrupt = () =>
    run(async () => {
      await post(`/api/sessions/${sid}/interrupt`);
      return "interrupt queued";
    });

  const kill = () => {
    if (!confirm(`Kill ${sid}? The session terminates and cannot be resumed.`)) return;
    run(async () => {
      await post(`/api/sessions/${sid}/kill`);
      return "killed";
    });
  };

  const remove = () => {
    if (!confirm(`Delete ${sid}? The session and its history are removed permanently.`)) return;
    run(async () => {
      await post(`/api/sessions/${sid}/delete`);
      router.push("/sessions");
    });
  };

  const showPanel = panelOpen && artifacts.length > 0;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex flex-wrap items-center gap-1.5 border-b border-border bg-surface px-3 py-2 sm:gap-2.5 sm:px-5 sm:py-3">
        <span
          className={
            session?.title
              ? "min-w-0 max-w-full truncate text-[13px] font-medium"
              : "min-w-0 max-w-full truncate font-mono text-[13px]"
          }
          title={sid}
        >
          {session?.title || sid}
        </span>
        {session?.title && (
          <span className="font-mono text-[11px] text-muted-foreground" title={sid}>
            {shortId(sid)}
          </span>
        )}
        {session && (
          <>
            <StateBadge state={session.state} />
            {/* a task run is an ordinary session; say which workflow (and task)
                owns it so the transcript links back to its run history */}
            {session.workflow && (
              <Link
                href={`/workflow?name=${encodeURIComponent(session.workflow)}`}
                className="hover:opacity-80"
                title={`Task ${session.task ?? "?"} of workflow ${session.workflow} (${session.trigger})`}
              >
                <Badge className="font-mono">
                  <CalendarClock className="size-3" />
                  {session.workflow}
                  {session.task && <span className="text-muted-foreground">/{session.task}</span>}
                </Badge>
              </Link>
            )}
            {/* which stored agent the session's options were resolved from */}
            {session.agent && (
              <Link
                href={`/agent?name=${encodeURIComponent(session.agent)}`}
                className="hover:opacity-80"
                title={`Ran as agent ${session.agent}`}
              >
                <Badge className="font-mono">
                  <Bot className="size-3" />
                  {session.agent}
                </Badge>
              </Link>
            )}
            <span className="font-mono text-[11px] text-muted-foreground">
              {session.model || ""} {cost(session.cost_usd)}
              {session.stop_reason ? ` · ${session.stop_reason}` : ""}
            </span>
          </>
        )}
        <span className="flex-1" />
        {artifacts.length > 0 && (
          <Button
            variant="outline"
            size="sm"
            onClick={() => setPanelOpen((open) => !open)}
            title={`Artifacts (${artifacts.length})`}
          >
            <PanelRight /> <span className="hidden sm:inline">Artifacts ({artifacts.length})</span>
          </Button>
        )}
        <Button
          variant="outline"
          size="sm"
          onClick={interrupt}
          disabled={!session || dead}
          title="Interrupt"
        >
          <CircleStop /> <span className="hidden sm:inline">Interrupt</span>
        </Button>
        <Button variant="destructive" size="sm" onClick={kill} disabled={!session || dead} title="Kill">
          <OctagonX /> <span className="hidden sm:inline">Kill</span>
        </Button>
        <Button
          variant="destructive"
          size="sm"
          onClick={remove}
          disabled={!session || session.state === "running"}
          title={session?.state === "running" ? "Kill the session before deleting it" : "Delete"}
        >
          <Trash2 /> <span className="hidden sm:inline">Delete</span>
        </Button>
      </div>

      {session?.summary && (
        <p className="border-b border-border bg-surface px-3 pb-2 text-[12px] text-muted-foreground sm:px-5">
          {session.summary}
        </p>
      )}

      {/* Below lg an open panel takes the row over and the transcript hides —
          but approvals and the composer live outside it, so a pending decision
          can never be hidden behind a document while its deadline runs down. */}
      <div className="flex min-h-0 flex-1">
        <div
          className={
            showPanel
              ? "hidden min-h-0 min-w-0 flex-1 flex-col lg:flex"
              : "flex min-h-0 min-w-0 flex-1 flex-col"
          }
        >
          <Transcript
            events={events}
            placeholder={session ? "No messages yet." : "loading…"}
            pending={pending}
            working={working}
            artifactPaths={artifactPaths}
            onOpenArtifact={openArtifact}
          />
        </div>

        {showPanel && (
          <ArtifactPanel
            artifacts={artifacts}
            selectedPath={selectedPath}
            onSelect={setSelectedPath}
            onClose={() => setPanelOpen(false)}
          />
        )}
      </div>

      {approvals.length > 0 && (
        <div className="mx-auto w-full max-w-3xl space-y-2.5 px-5 pt-2.5">
          {approvals.map((approval) => (
            <ApprovalCard key={approval.call_hash} approval={approval} now={now} onDecide={decide} />
          ))}
        </div>
      )}

      {flash && <p className="pt-1.5 text-center text-[11px] text-muted-foreground">{flash}</p>}

      <Composer disabled={!session || dead} onSend={sendPrompt} />
    </div>
  );
}
