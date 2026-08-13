"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { CircleStop, OctagonX, PanelRight } from "lucide-react";
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
  const { session, events, approvals, removeApproval } = useSessionPoll(sid);
  const now = useNow();
  const [flash, run] = useAction();
  const dead = session?.state === "terminated";

  // Artifacts replay from the transcript (see lib/artifacts.ts). The panel
  // opens itself when a version lands and stays closed once dismissed, until
  // the next write — mirroring how claude.ai surfaces artifacts.
  const artifacts = useMemo(() => deriveArtifacts(events), [events]);
  const [panelOpen, setPanelOpen] = useState(false);
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const versionCount = artifacts.reduce((n, a) => n + a.versions.length, 0);
  const seenVersionsRef = useRef(0);
  useEffect(() => {
    if (versionCount > seenVersionsRef.current) {
      seenVersionsRef.current = versionCount;
      setSelectedPath(artifacts[0].path); // most recently updated
      setPanelOpen(true);
    }
  }, [versionCount, artifacts]);
  useEffect(() => {
    // new sid: the poll hook resets events, so reset the artifact state too
    seenVersionsRef.current = 0;
    setPanelOpen(false);
    setSelectedPath(null);
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
      const result = await post<{ triggered: boolean }>(`/api/sessions/${sid}/prompt`, { text });
      return result.triggered ? "queued · runner starting…" : "queued";
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

  const showPanel = panelOpen && artifacts.length > 0;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex flex-wrap items-center gap-2.5 border-b border-border bg-surface px-5 py-3">
        <span className="font-mono text-[13px]">{sid}</span>
        {session && (
          <>
            <StateBadge state={session.state} />
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
      </div>

      <div className="flex min-h-0 flex-1">
        {/* on small screens the open panel takes over; the transcript column hides */}
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
            onOpenArtifact={openArtifact}
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

          {flash && <p className="pt-1.5 text-center text-[11px] text-muted-foreground">{flash}</p>}

          <Composer disabled={!session || dead} onSend={sendPrompt} />
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
    </div>
  );
}
