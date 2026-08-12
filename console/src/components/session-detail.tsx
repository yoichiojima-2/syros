"use client";

import { CircleStop, OctagonX } from "lucide-react";
import { Button } from "@/components/ui/button";
import { StateBadge } from "@/components/state-badge";
import { Transcript } from "@/components/transcript";
import { Composer } from "@/components/composer";
import { ApprovalCard } from "@/components/approval-card";
import { useFlash, useNow, useSessionPoll } from "@/lib/hooks";
import { post } from "@/lib/api";
import { cost } from "@/lib/format";

export function SessionDetail({ sid }: { sid: string }) {
  const { session, events, approvals, removeApproval } = useSessionPoll(sid);
  const now = useNow();
  const [flash, showFlash] = useFlash();
  const dead = session?.state === "terminated";

  const decide = async (callHash: string, allow: boolean, message: string | null) => {
    try {
      await post(`/api/sessions/${sid}/approvals/${callHash}`, { allow, message });
      // Drop the card immediately; a decided approval leaves the pending set
      // server-side, so the next poll agrees.
      removeApproval(callHash);
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

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex flex-wrap items-center gap-2.5 border-b border-border bg-card px-5 py-2.5">
        <span className="font-mono text-[13px] font-semibold">{sid}</span>
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
        <Button variant="outline" size="sm" onClick={interrupt} disabled={!session || dead}>
          <CircleStop /> Interrupt
        </Button>
        <Button variant="destructive" size="sm" onClick={kill} disabled={!session || dead}>
          <OctagonX /> Kill
        </Button>
      </div>

      <Transcript
        events={events}
        placeholder={session ? "No messages yet." : "loading…"}
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

      {flash && <p className="pt-1.5 text-center font-mono text-[11px] text-muted-foreground">{flash}</p>}

      <Composer disabled={!session || dead} onSend={sendPrompt} />
    </div>
  );
}
