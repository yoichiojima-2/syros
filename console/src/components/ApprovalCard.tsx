import { useState } from "react";
import type { Approval } from "../types";

interface Props {
  approval: Approval;
  now: number;
  onDecide: (callHash: string, allow: boolean, message: string | null) => void;
}

export default function ApprovalCard({ approval, now, onDecide }: Props) {
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
      className={`relative overflow-hidden rounded-xl border bg-panel px-3.5 pt-3 pb-3.5 ${
        urgent ? "border-danger/70" : "border-warn/70"
      }`}
    >
      <div
        className={`absolute top-0 left-0 h-[3px] transition-[width] duration-500 ease-linear ${
          urgent ? "bg-danger" : "bg-warn"
        }`}
        style={{ width: `${(left / total) * 100}%` }}
      />
      <div className="flex items-baseline gap-2.5">
        <b className={`font-mono text-[13px] font-semibold ${urgent ? "text-danger" : "text-warn"}`}>
          {approval.tool_name}
        </b>
        <span className="font-mono text-[10px] font-semibold tracking-[0.14em] text-dim uppercase">
          approval
        </span>
        <span className={`ml-auto font-mono text-xs ${urgent ? "text-danger" : "text-dim"}`}>
          {left > 0 ? `${minutes}:${seconds} left` : "expired"}
        </span>
      </div>
      <pre className="my-2 max-h-40 overflow-y-auto font-mono text-xs whitespace-pre-wrap text-dim [overflow-wrap:break-word]">
        {JSON.stringify(approval.input, null, 2)}
      </pre>
      <div className="flex gap-2">
        <button
          type="button"
          onClick={() => onDecide(approval.call_hash, true, null)}
          className="rounded-md border border-ok/70 bg-raised px-3 py-1 text-[13px] text-ok transition-colors hover:bg-ok/10"
        >
          Allow
        </button>
        <button
          type="button"
          onClick={() => onDecide(approval.call_hash, false, reason || null)}
          className="rounded-md border border-line bg-raised px-3 py-1 text-[13px] transition-colors hover:border-danger hover:text-danger"
        >
          Deny
        </button>
        <input
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder="deny reason (optional)"
          className="min-w-0 flex-1 rounded-md border border-line bg-bg px-2.5 py-1 font-mono text-xs text-ink placeholder:text-faint"
        />
      </div>
    </div>
  );
}
