"use client";

import { useState } from "react";
import { ChevronLeft, ChevronRight, Download, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  ArtifactFrame,
  CopyButton,
  download,
  FullscreenButton,
  useFullscreen,
} from "@/components/artifact-viewer";
import { artifactLabel, type Artifact } from "@/lib/artifacts";
import { cn } from "@/lib/utils";

// Claude-style artifact panel: workspace files the agent wrote, previewed
// beside the transcript via the shared sandboxed-iframe viewer
// (artifact-viewer.tsx). Everything unpreviewable shows as source.

export function ArtifactPanel({
  artifacts,
  selectedPath,
  onSelect,
  onClose,
}: {
  artifacts: Artifact[];
  selectedPath: string | null;
  onSelect: (path: string) => void;
  onClose: () => void;
}) {
  // null cursor = follow the newest version as it streams in; stepping back
  // pins a specific version, stepping forward to the head resumes following.
  const [cursor, setCursor] = useState<{ path: string; index: number } | null>(null);
  const [showSource, setShowSource] = useState(false);
  const [fullscreen, setFullscreen] = useFullscreen();

  const artifact = artifacts.find((a) => a.path === selectedPath) || artifacts[0];
  if (!artifact) return null;

  const head = artifact.versions.length - 1;
  const pinned = cursor && cursor.path === artifact.path && cursor.index < head;
  const index = pinned ? cursor.index : head;
  const version = artifact.versions[index];
  const step = (next: number) =>
    setCursor(next >= head ? null : { path: artifact.path, index: Math.max(0, next) });

  const previewable = artifact.kind !== "code";
  const source = !previewable || showSource;

  return (
    <aside
      className={cn(
        "flex flex-col bg-card",
        fullscreen
          ? "fixed inset-0 z-50"
          : "w-full lg:w-[min(46%,660px)] lg:border-l lg:border-border",
      )}
    >
      <div className="flex flex-wrap items-center gap-x-1.5 gap-y-1 border-b border-border px-3 py-2">
        {artifacts.length > 1 ? (
          <select
            value={artifact.path}
            onChange={(e) => onSelect(e.target.value)}
            title={artifact.path}
            className="min-w-0 flex-1 truncate rounded-md border border-transparent bg-transparent px-1.5 py-1 font-mono text-[13px] font-medium hover:border-input focus:outline-none"
          >
            {artifacts.map((a) => (
              <option key={a.path} value={a.path} title={a.path}>
                {artifactLabel(a, artifacts)}
              </option>
            ))}
          </select>
        ) : (
          <span
            className="min-w-0 flex-1 truncate px-1.5 font-mono text-[13px] font-medium"
            title={artifact.path}
          >
            {artifact.name}
          </span>
        )}
        {artifact.stale && (
          <span
            className="rounded-md bg-warn/15 px-1.5 py-0.5 font-mono text-[10px] text-warn"
            title="An edit to this file couldn't be replayed — something outside Write/Edit changed it, so the workspace copy has diverged from what's shown here."
          >
            diverged
          </span>
        )}
        {artifact.versions.length > 1 && (
          <span className="flex items-center font-mono text-[11px] text-muted-foreground">
            <Button variant="ghost" size="sm" disabled={index === 0} onClick={() => step(index - 1)}>
              <ChevronLeft />
            </Button>
            v{index + 1}/{artifact.versions.length}
            <Button variant="ghost" size="sm" disabled={index === head} onClick={() => step(index + 1)}>
              <ChevronRight />
            </Button>
          </span>
        )}
        {previewable && (
          <div className="flex rounded-lg bg-secondary p-0.5 font-mono text-[11px]">
            {(["preview", "source"] as const).map((mode) => (
              <button
                key={mode}
                onClick={() => setShowSource(mode === "source")}
                className={cn(
                  "rounded-md px-2 py-0.5 transition-colors",
                  source === (mode === "source")
                    ? "bg-card shadow-sm"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                {mode}
              </button>
            ))}
          </div>
        )}
        <CopyButton text={version.content} />
        <Button
          variant="ghost"
          size="sm"
          title="Download"
          onClick={() => download(artifact.name, version.content)}
        >
          <Download />
        </Button>
        <FullscreenButton fullscreen={fullscreen} onToggle={setFullscreen} />
        <Button variant="ghost" size="sm" title="Close" onClick={onClose}>
          <X />
        </Button>
      </div>

      {source ? (
        <pre className="min-h-0 flex-1 overflow-auto px-4 py-3 font-mono text-xs leading-relaxed whitespace-pre">
          {version.content}
        </pre>
      ) : (
        <ArtifactFrame
          name={artifact.name}
          kind={artifact.kind}
          content={version.content}
          // keyed on the version index, not seq — one assistant message can
          // carry two writes to the same file, which share a seq.
          frameKey={`${artifact.path}:${index}`}
        />
      )}
    </aside>
  );
}
