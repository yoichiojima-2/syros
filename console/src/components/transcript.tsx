"use client";

import { useLayoutEffect, useRef } from "react";
import { FileCode2 } from "lucide-react";
import type { ContentBlock, TranscriptEvent, TranscriptMessage } from "@/lib/types";
import { artifactToolPath } from "@/lib/artifacts";
import { compact, cost, pretty } from "@/lib/format";
import { renderMarkdown } from "@/lib/markdown";
import { cn } from "@/lib/utils";

// Renders the event feed. Message kinds and block types mirror the Firestore
// serialization in src/syros/types.py; unknown ones render as null rather
// than breaking the transcript.
//
// Layout follows a chat, not a log: the prompt sits in a tinted bubble on the
// right, the agent answers as plain prose on the page, and the machinery (tool
// calls, results, thinking) stays folded away in muted disclosures.

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
    <details className={cn("group font-mono text-xs", className)}>
      {/* negative margin keeps the label on the prose margin while the hover
          fill still reads as a padded row */}
      <summary className="-mx-2 overflow-hidden rounded-lg px-2 py-1 text-ellipsis whitespace-nowrap text-muted-foreground transition-colors hover:bg-secondary/60">
        {summary}
      </summary>
      <pre className="mt-1.5 overflow-x-auto rounded-xl border border-border bg-surface px-3 py-2.5 whitespace-pre-wrap [overflow-wrap:break-word]">
        {detail}
      </pre>
    </details>
  );
}

function UserText({ text }: { text: string }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[85%] rounded-2xl bg-secondary px-4 py-2.5 text-[15px] leading-relaxed whitespace-pre-wrap [overflow-wrap:break-word]">
        {text}
      </div>
    </div>
  );
}

// A Write/Edit call renders as a Claude-style artifact card instead of a raw
// tool row: the payload is a document, and the panel is where you read it.
function ArtifactCard({
  block,
  path,
  onOpen,
}: {
  block: ContentBlock;
  path: string;
  onOpen: (path: string) => void;
}) {
  return (
    <button
      onClick={() => onOpen(path)}
      className="flex w-full max-w-sm items-center gap-3 rounded-xl border border-border bg-card px-3.5 py-2.5 text-left transition-colors hover:border-input hover:bg-secondary/40"
    >
      <FileCode2 className="size-5 shrink-0 text-muted-foreground" />
      <span className="min-w-0 flex-1">
        <span className="block truncate font-mono text-[13px] font-medium">
          {path.split("/").pop()}
        </span>
        <span className="block text-[11px] text-muted-foreground">
          {block.name} · click to open
        </span>
      </span>
    </button>
  );
}

function MessageView({
  message,
  artifactPaths,
  onOpenArtifact,
}: {
  message: TranscriptMessage;
  artifactPaths?: ReadonlySet<string>;
  onOpenArtifact?: (path: string) => void;
}) {
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
                className="chat-prose text-[15px] leading-7 [overflow-wrap:break-word]"
                dangerouslySetInnerHTML={{ __html: renderMarkdown(block.text || "") }}
              />
            );
          if (block.type === "thinking")
            return (
              <details key={i} className="text-[13px] text-muted-foreground">
                <summary className="text-[11px] tracking-wide uppercase">thinking</summary>
                <div className="mt-1.5 border-l-2 border-border pl-3 whitespace-pre-wrap italic">
                  {block.thinking || ""}
                </div>
              </details>
            );
          if (block.type === "tool_use" || block.type === "server_tool_use") {
            // Only a write that actually landed becomes a card. A denied,
            // pending, or unreplayable one keeps its tool row, so its input
            // stays inspectable and no card opens a different document.
            const path = artifactToolPath(block);
            if (onOpenArtifact && path && artifactPaths?.has(path))
              return <ArtifactCard key={i} block={block} path={path} onOpen={onOpenArtifact} />;
            return (
              <CollapsibleRow
                key={i}
                summary={
                  <>
                    <b className="font-semibold text-foreground">{block.name || "?"}</b>{" "}
                    {compact(block.input || {}, 140)}
                  </>
                }
                detail={JSON.stringify(block.input || {}, null, 2)}
              />
            );
          }
          return null;
        })}
      </>
    );
  }
  if (message.kind === "system") {
    return (
      <div className="font-mono text-[11px] text-faint">system: {message.subtype || ""}</div>
    );
  }
  if (message.kind === "result") {
    const parts = [`${message.subtype} · ${message.num_turns} turns`];
    if (message.total_cost_usd != null) parts.push(cost(message.total_cost_usd));
    if (message.duration_ms != null) parts.push(`${(message.duration_ms / 1000).toFixed(1)}s`);
    return (
      <div className="flex items-center gap-3 pt-1">
        <span className="h-px flex-1 bg-border" />
        <span
          className={cn(
            "font-mono text-[11px]",
            message.is_error ? "text-destructive" : "text-faint",
          )}
        >
          {parts.join(" · ")}
        </span>
        <span className="h-px flex-1 bg-border" />
      </div>
    );
  }
  return null;
}

export function Transcript({
  events,
  placeholder,
  artifactPaths,
  onOpenArtifact,
}: {
  events: TranscriptEvent[];
  placeholder: string | null;
  artifactPaths?: ReadonlySet<string>;
  onOpenArtifact?: (path: string) => void;
}) {
  const boxRef = useRef<HTMLDivElement>(null);
  // Follow new messages only while the user is at the bottom; once they
  // scroll up to read, stop yanking the view down on every poll.
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
        // 48px of slack so a near-bottom position still counts as pinned.
        pinnedRef.current = box.scrollTop + box.clientHeight >= box.scrollHeight - 48;
      }}
      className="min-h-0 flex-1 overflow-y-auto px-5 py-6"
    >
      <div className="mx-auto max-w-3xl space-y-5">
        {placeholder !== null && events.length === 0 && (
          <p className="pt-16 text-center text-[13px] text-muted-foreground">{placeholder}</p>
        )}
        {events.map((event) => (
          <MessageView
            key={event.seq}
            message={event.message || {}}
            artifactPaths={artifactPaths}
            onOpenArtifact={onOpenArtifact}
          />
        ))}
      </div>
    </div>
  );
}
