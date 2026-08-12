"use client";

import { useState } from "react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { shortId } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { Approval } from "@/lib/types";

export function ApprovalCard({
  approval,
  now,
  sessionId,
  onDecide,
}: {
  approval: Approval;
  now: number;
  /** set on the global queue page, where each card links to its session */
  sessionId?: string;
  onDecide: (callHash: string, allow: boolean, message: string | null) => void;
}) {
  const [reason, setReason] = useState("");
  const total = Math.max(1, approval.deadline - approval.requested_at);
  const left = Math.max(0, approval.deadline - now);
  const urgent = left < 60;
  const minutes = Math.floor(left / 60);
  const seconds = Math.floor(left % 60)
    .toString()
    .padStart(2, "0");

  return (
    <div
      className={cn(
        "relative overflow-hidden rounded-xl border bg-card px-3.5 pt-3 pb-3.5",
        urgent ? "border-destructive/70" : "border-warn/70",
      )}
    >
      <div
        className={cn(
          "absolute top-0 left-0 h-[3px] transition-[width] duration-500 ease-linear",
          urgent ? "bg-destructive" : "bg-warn-dot",
        )}
        style={{ width: `${(left / total) * 100}%` }}
      />
      <div className="flex items-baseline gap-2.5">
        <b
          className={cn(
            "font-mono text-[13px] font-semibold",
            urgent ? "text-destructive" : "text-warn",
          )}
        >
          {approval.tool_name}
        </b>
        <span className="font-mono text-[10px] font-semibold tracking-[0.14em] text-muted-foreground uppercase">
          approval
        </span>
        {sessionId && (
          <Link
            href={`/session?sid=${sessionId}`}
            className="font-mono text-[11px] text-muted-foreground underline-offset-2 hover:underline"
          >
            {shortId(sessionId)}
          </Link>
        )}
        <span
          className={cn(
            "ml-auto font-mono text-xs tabular-nums",
            urgent ? "text-destructive" : "text-muted-foreground",
          )}
        >
          {left > 0 ? `${minutes}:${seconds} left` : "expired"}
        </span>
      </div>
      <pre className="my-2 max-h-40 overflow-y-auto font-mono text-xs whitespace-pre-wrap text-muted-foreground [overflow-wrap:break-word]">
        {JSON.stringify(approval.input, null, 2)}
      </pre>
      <div className="flex gap-2">
        <Button variant="ok" onClick={() => onDecide(approval.call_hash, true, null)}>
          Allow
        </Button>
        <Button
          variant="destructive"
          onClick={() => onDecide(approval.call_hash, false, reason || null)}
        >
          Deny
        </Button>
        <Input
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder="deny reason (optional)"
          className="flex-1 font-mono text-xs"
        />
      </div>
    </div>
  );
}
