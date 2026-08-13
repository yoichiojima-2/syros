"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { CalendarClock, CircleStop, OctagonX, PanelRight, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { StateBadge } from "@/components/state-badge";
import { Transcript } from "@/components/transcript";
import { Composer } from "@/components/composer";
import { ApprovalCard } from "@/components/approval-card";
import { ArtifactPanel } from "@/components/artifact-panel";
import { deriveArtifacts } from "@/lib/artifacts";
import { useAction, useNow, useSessionPoll } from "@/lib/hooks";
import { post } from "@/lib/api";
import { cost } from "@/lib/format";

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
        const message = event.message;
        if (message?.kind !== "user" || typeof message.content !== "string") continue;
        const i = next.indexOf(message.content);
        if (i !== -1) next = [...next.slice(0, i), ...next.slice(i + 1)];
      }
      return next;
    });
  }, [events]);

  // The agent owes a response while a prompt waits in the inbox or a live
  // turn hasn't reached its result row yet — that's when the typing dots show.
  const lastKind = events.length ? events[events.length - 1].message?.kind : undefined;
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

  const sendPrompt = (text: string) =>
    run(async () => {
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
        throw err;
      }
    });

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
      <div className="flex flex-wrap items-center gap-2.5 border-b border-border bg-surface px-5 py-3">
        <span className="font-mono text-[13px]">{sid}</span>
        {session && (
          <>
            <StateBadge state={session.state} />
            {/* a scheduled run is an ordinary session; say which deployment owns
                it so the transcript links back to its history */}
            {session.deployment && (
              <Link
                href={`/deployment?name=${encodeURIComponent(session.deployment)}`}
                className="hover:opacity-80"
                title={`Run of deployment ${session.deployment} (${session.trigger})`}
              >
                <Badge className="font-mono">
                  <CalendarClock className="size-3" />
                  {session.deployment}
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
          <Button variant="outline" size="sm" onClick={() => setPanelOpen((open) => !open)}>
            <PanelRight /> Artifacts ({artifacts.length})
          </Button>
        )}
        <Button variant="outline" size="sm" onClick={interrupt} disabled={!session || dead}>
          <CircleStop /> Interrupt
        </Button>
        <Button variant="destructive" size="sm" onClick={kill} disabled={!session || dead}>
          <OctagonX /> Kill
        </Button>
        <Button
          variant="destructive"
          size="sm"
          onClick={remove}
          disabled={!session || session.state === "running"}
          title={session?.state === "running" ? "Kill the session before deleting it" : undefined}
        >
          <Trash2 /> Delete
        </Button>
      </div>

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
