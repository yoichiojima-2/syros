"use client";

import { useLayoutEffect, useRef } from "react";
import type { ContentBlock, TranscriptEvent, TranscriptMessage } from "@/lib/types";
import { compact, cost, pretty } from "@/lib/format";
import { cn } from "@/lib/utils";

function blockList(content: TranscriptMessage["content"]): ContentBlock[] {
  return Array.isArray(content) ? content : [];
}

function CollapsibleRow({
  className,
  summary,
  detail,
}: {
  className?: string;
  summary: React.ReactNode;
  detail: string;
}) {
  return (
    <details className={cn("font-mono text-xs", className)}>
      <summary className="overflow-hidden text-ellipsis whitespace-nowrap text-muted-foreground">
        {summary}
      </summary>
      <pre className="mt-1.5 overflow-x-auto rounded-lg border border-border bg-card px-3 py-2.5 whitespace-pre-wrap [overflow-wrap:break-word]">
        {detail}
      </pre>
    </details>
  );
}

function UserText({ text }: { text: string }) {
  return (
    <div className="rounded-lg border border-border bg-card px-3 py-2 font-mono text-[13px] whitespace-pre-wrap [overflow-wrap:break-word]">
      <span className="text-faint">❯ </span>
      {text}
    </div>
  );
}

function MessageView({ message }: { message: TranscriptMessage }) {
  if (message.kind === "user") {
    return (
      <>
        {typeof message.content === "string" && <UserText text={message.content} />}
        {blockList(message.content).map((block, i) => {
          if (block.type === "text") return <UserText key={i} text={block.text || ""} />;
          if (block.type === "tool_result")
            return (
              <CollapsibleRow
                key={i}
                className={block.is_error ? "[&>summary]:text-destructive" : ""}
                summary={`↳ ${compact(block.content, 160)}`}
                detail={pretty(block.content)}
              />
            );
          return null;
        })}
      </>
    );
  }
  if (message.kind === "assistant") {
    return (
      <>
        {blockList(message.content).map((block, i) => {
          if (block.type === "text")
            return (
              <div
                key={i}
                className="text-sm leading-relaxed whitespace-pre-wrap [overflow-wrap:break-word]"
              >
                {block.text}
              </div>
            );
          if (block.type === "thinking")
            return (
              <details key={i} className="text-[13px] text-muted-foreground">
                <summary className="font-mono text-[11px]">thinking</summary>
                <div className="pt-1.5 pl-3 whitespace-pre-wrap italic">{block.thinking || ""}</div>
              </details>
            );
          if (block.type === "tool_use" || block.type === "server_tool_use")
            return (
              <CollapsibleRow
                key={i}
                summary={
                  <>
                    ⚙ <b className="font-semibold text-foreground">{block.name || "?"}</b>{" "}
                    {compact(block.input || {}, 140)}
                  </>
                }
                detail={JSON.stringify(block.input || {}, null, 2)}
              />
            );
          return null;
        })}
      </>
    );
  }
  if (message.kind === "system") {
    return (
      <div className="font-mono text-[11px] text-muted-foreground">
        system: {message.subtype || ""}
      </div>
    );
  }
  if (message.kind === "result") {
    const parts = [`[${message.subtype}] ${message.num_turns} turns`];
    if (message.total_cost_usd != null) parts.push(cost(message.total_cost_usd));
    if (message.duration_ms != null) parts.push(`${(message.duration_ms / 1000).toFixed(1)}s`);
    return (
      <div
        className={cn(
          "border-t border-border pt-2 font-mono text-xs",
          message.is_error ? "text-destructive" : "text-muted-foreground",
        )}
      >
        {parts.join(" · ")}
      </div>
    );
  }
  return null;
}

export function Transcript({
  events,
  placeholder,
}: {
  events: TranscriptEvent[];
  placeholder: string | null;
}) {
  const boxRef = useRef<HTMLDivElement>(null);
  const pinnedRef = useRef(true);

  // auto-scroll only while the reader is pinned near the bottom
  useLayoutEffect(() => {
    const box = boxRef.current;
    if (box && pinnedRef.current) box.scrollTop = box.scrollHeight;
  }, [events]);

  return (
    <div
      ref={boxRef}
      onScroll={() => {
        const box = boxRef.current!;
        pinnedRef.current = box.scrollTop + box.clientHeight >= box.scrollHeight - 48;
      }}
      className="min-h-0 flex-1 overflow-y-auto p-5"
    >
      <div className="mx-auto max-w-3xl space-y-3">
        {placeholder !== null && events.length === 0 && (
          <p className="pt-16 text-center text-[13px] text-muted-foreground">{placeholder}</p>
        )}
        {events.map((event) => (
          <MessageView key={event.seq} message={event.message || {}} />
        ))}
      </div>
    </div>
  );
}
