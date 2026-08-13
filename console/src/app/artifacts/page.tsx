"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Download, FileCode2, Package } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  ArtifactFrame,
  CopyButton,
  download,
  FullscreenButton,
  useFullscreen,
} from "@/components/artifact-viewer";
import { useArtifactSpaces, useNow, useSpaceArtifacts } from "@/lib/hooks";
import { previewKind, type PreviewKind } from "@/lib/artifacts";
import { bytes, relTime } from "@/lib/format";
import type { StoredFile } from "@/lib/types";
import { cn } from "@/lib/utils";

// Shared artifact spaces (artifacts/{space}/ in the bucket) — the publish
// surface agents write into. Master–detail: spaces and files on the left,
// the shared sandboxed-iframe preview on the right. Deep-linkable via
// ?space=&file= so a session (or a teammate) can hand out a URL to a report.

const IMAGE_EXTENSIONS = new Set(["png", "jpg", "jpeg", "gif", "webp", "ico", "bmp", "avif"]);

function fileUrl(space: string, name: string): string {
  return `/api/artifacts/${space}/file?name=${encodeURIComponent(name)}`;
}

function isImage(name: string): boolean {
  const dot = name.lastIndexOf(".");
  return dot >= 0 && IMAGE_EXTENSIONS.has(name.slice(dot + 1).toLowerCase());
}

export default function ArtifactsPage() {
  // useSearchParams requires a Suspense boundary under static export
  return (
    <Suspense>
      <ArtifactsInner />
    </Suspense>
  );
}

function ArtifactsInner() {
  const router = useRouter();
  const params = useSearchParams();
  const space = params.get("space");
  const file = params.get("file");
  const spaces = useArtifactSpaces();
  const files = useSpaceArtifacts(space);
  const now = useNow();

  const select = (nextSpace: string | null, nextFile: string | null) => {
    const query = new URLSearchParams();
    if (nextSpace) query.set("space", nextSpace);
    if (nextFile) query.set("file", nextFile);
    router.replace(`/artifacts${query.size ? `?${query}` : ""}`);
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
      <aside className="flex shrink-0 flex-col gap-4 overflow-y-auto border-b border-border p-4 lg:w-72 lg:border-r lg:border-b-0">
        <div>
          <h1 className="px-1 pb-2 text-xl font-semibold tracking-tight">Artifacts</h1>
          {spaces === null ? (
            <div className="space-y-2">
              <Skeleton className="h-7" />
              <Skeleton className="h-7" />
            </div>
          ) : spaces.length === 0 ? (
            <p className="px-1 text-[12px] text-muted-foreground">
              No artifact spaces yet — sessions created with{" "}
              <code className="font-mono text-[11px]">AgentOptions(artifacts=&quot;name&quot;)</code>{" "}
              publish here, and <code className="font-mono text-[11px]">syros artifacts</code> pushes
              from the CLI.
            </p>
          ) : (
            <ul className="space-y-0.5">
              {spaces.map((s) => (
                <li key={s.name}>
                  <button
                    onClick={() => select(s.name, null)}
                    className={cn(
                      "flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-[13px] transition-colors",
                      s.name === space
                        ? "bg-primary-soft font-medium"
                        : "text-muted-foreground hover:bg-secondary/70 hover:text-foreground",
                    )}
                  >
                    <Package className={cn("size-4", s.name === space && "text-primary")} />
                    <span className="min-w-0 flex-1 truncate font-mono">{s.name}</span>
                    <span className="font-mono text-[11px] text-faint">{s.file_count}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
        {space && (
          <div>
            <h2 className="px-1 pb-1.5 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
              {space}
            </h2>
            {files === null ? (
              <Skeleton className="h-7" />
            ) : files.length === 0 ? (
              <p className="px-1 text-[12px] text-muted-foreground">empty</p>
            ) : (
              <ul className="space-y-0.5">
                {files.map((f) => (
                  <li key={f.name}>
                    <button
                      onClick={() => select(space, f.name)}
                      title={f.name}
                      className={cn(
                        "flex w-full items-baseline gap-2 rounded-lg px-2 py-1.5 text-left font-mono text-[12px] transition-colors",
                        f.name === file
                          ? "bg-primary-soft font-medium"
                          : "text-muted-foreground hover:bg-secondary/70 hover:text-foreground",
                      )}
                    >
                      <span className="min-w-0 flex-1 truncate">{f.name}</span>
                      <span className="shrink-0 text-[10px] text-faint">
                        {relTime(f.updated, now) || bytes(f.size)}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </aside>
      <main className="flex min-h-0 min-w-0 flex-1 flex-col">
        {space && file ? (
          <FileViewer space={space} file={file} files={files} />
        ) : (
          <div className="flex flex-1 flex-col items-center justify-center gap-2 text-muted-foreground">
            <FileCode2 className="size-6" />
            <p className="text-[13px]">
              {space ? "Pick a file to preview it." : "Pick an artifact space."}
            </p>
          </div>
        )}
      </main>
    </div>
  );
}

function FileViewer({
  space,
  file,
  files,
}: {
  space: string;
  file: string;
  files: StoredFile[] | null;
}) {
  const kind: PreviewKind = previewKind(file);
  const image = isImage(file);
  const textual = !image; // "code" fetches as text too — it shows as source
  const [content, setContent] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showSource, setShowSource] = useState(false);
  const [fullscreen, setFullscreen] = useFullscreen();
  const updated = files?.find((f) => f.name === file)?.updated ?? null;

  useEffect(() => {
    setContent(null);
    setError(null);
    setShowSource(false);
    if (!textual) return;
    let cancelled = false;
    fetch(fileUrl(space, file))
      .then(async (response) => {
        if (response.status === 413) throw new Error("too large to preview — download instead");
        if (!response.ok) {
          const body = await response.json().catch(() => null);
          throw new Error(body?.error || `HTTP ${response.status}`);
        }
        return response.text();
      })
      .then((text) => {
        if (!cancelled) setContent(text);
      })
      .catch((err: Error) => {
        if (!cancelled) setError(err.message);
      });
    return () => {
      cancelled = true;
    };
    // refetch when the blob changes upstream, not on every poll
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [space, file, updated]);

  const previewable = kind !== "code";
  const source = !previewable || showSource;

  return (
    <div
      className={cn(
        "flex min-h-0 min-w-0 flex-1 flex-col",
        fullscreen && "fixed inset-0 z-50 bg-card",
      )}
    >
      <div className="flex items-center gap-1.5 border-b border-border px-3 py-2">
        <span className="min-w-0 flex-1 truncate px-1.5 font-mono text-[13px] font-medium" title={file}>
          {file}
        </span>
        {previewable && !image && (
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
        {content !== null && <CopyButton text={content} />}
        <Button
          variant="ghost"
          size="sm"
          title="Download"
          onClick={() => {
            if (content !== null) download(file.split("/").pop() || file, content);
            else window.open(fileUrl(space, file), "_blank");
          }}
        >
          <Download />
        </Button>
        <FullscreenButton fullscreen={fullscreen} onToggle={setFullscreen} />
      </div>
      {image ? (
        <div className="grid min-h-0 flex-1 place-items-center overflow-auto bg-card p-4">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={fileUrl(space, file)} alt={file} className="max-h-full max-w-full" />
        </div>
      ) : error ? (
        <p className="p-6 text-center text-[13px] text-muted-foreground">{error}</p>
      ) : content === null ? (
        <div className="flex-1 p-4">
          <Skeleton className="h-24" />
        </div>
      ) : source ? (
        <pre className="min-h-0 flex-1 overflow-auto px-4 py-3 font-mono text-xs leading-relaxed whitespace-pre">
          {content}
        </pre>
      ) : (
        <ArtifactFrame
          name={file}
          kind={kind}
          content={content}
          frameKey={`${space}/${file}:${updated ?? ""}`}
        />
      )}
    </div>
  );
}
