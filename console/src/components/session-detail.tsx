"use client";

import { CircleStop, OctagonX } from "lucide-react";
import { Button } from "@/components/ui/button";
import { StateBadge } from "@/components/state-badge";
import { Transcript } from "@/components/transcript";
import { Composer } from "@/components/composer";
import { ApprovalCard } from "@/components/approval-card";
import { useAction, useNow, useSessionPoll } from "@/lib/hooks";
import { post } from "@/lib/api";
import { cost } from "@/lib/format";

export function SessionDetail({ sid }: { sid: string }) {
  const { session, events, approvals, removeApproval } = useSessionPoll(sid);
  const now = useNow();
  const [flash, run] = useAction();
  const dead = session?.state === "terminated";

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

      {flash && <p className="pt-1.5 text-center text-[11px] text-muted-foreground">{flash}</p>}

      <Composer disabled={!session || dead} onSend={sendPrompt} />
    </div>
  );
}
