"use client";

import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ApprovalCard } from "@/components/approval-card";
import { useAction, useApprovals, useNow } from "@/lib/hooks";
import { post } from "@/lib/api";

export default function ApprovalsPage() {
  const { approvals, remove } = useApprovals();
  const now = useNow();
  const [flash, run] = useAction();

  const decide = (sessionId: string, callHash: string, allow: boolean, message: string | null) =>
    run(async () => {
      await post(`/api/sessions/${sessionId}/approvals/${callHash}`, { allow, message });
      remove(callHash);
      return allow ? "allowed" : "denied";
    });

  return (
    <div className="min-h-0 flex-1 space-y-5 overflow-y-auto p-6">
      <h1 className="text-2xl font-semibold tracking-tight">Approvals</h1>
      {flash && <p className="text-[11px] text-muted-foreground">{flash}</p>}
      {approvals === null ? (
        <Skeleton className="h-32 w-full max-w-3xl" />
      ) : approvals.length === 0 ? (
        <Card className="max-w-3xl">
          <CardContent className="py-16 text-center text-[13px] text-muted-foreground">
            No pending approvals — every tool call is either decided or hasn&apos;t asked yet.
          </CardContent>
        </Card>
      ) : (
        <div className="max-w-3xl space-y-3">
          {approvals.map((approval) => (
            <ApprovalCard
              key={approval.call_hash}
              approval={approval}
              now={now}
              sessionId={approval.session_id}
              onDecide={(callHash, allow, message) =>
                decide(approval.session_id, callHash, allow, message)
              }
            />
          ))}
        </div>
      )}
    </div>
  );
}
