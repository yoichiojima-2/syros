"use client";

import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ApprovalCard } from "@/components/approval-card";
import { useApprovals, useFlash, useNow } from "@/lib/hooks";
import { post } from "@/lib/api";

export default function ApprovalsPage() {
  const { approvals, remove } = useApprovals();
  const now = useNow();
  const [flash, showFlash] = useFlash();

  const decide = async (sessionId: string, callHash: string, allow: boolean, message: string | null) => {
    try {
      await post(`/api/sessions/${sessionId}/approvals/${callHash}`, { allow, message });
      remove(callHash);
      showFlash(allow ? "allowed" : "denied");
    } catch (err) {
      showFlash(`error: ${(err as Error).message}`);
    }
  };

  return (
    <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-5">
      <h1 className="text-lg font-semibold">Approvals</h1>
      {flash && <p className="font-mono text-[11px] text-muted-foreground">{flash}</p>}
      {approvals === null ? (
        <Skeleton className="h-32 w-full max-w-3xl" />
      ) : approvals.length === 0 ? (
        <Card className="max-w-3xl">
          <CardContent className="py-14 text-center text-[13px] text-muted-foreground">
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
