"use client";

import { useEffect, useState } from "react";
import { useTheme } from "next-themes";
import { marked } from "marked";
import { Check, ChevronLeft, ChevronRight, Copy, Download, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { artifactLabel, type Artifact } from "@/lib/artifacts";
import { cn } from "@/lib/utils";

// Claude-style artifact panel: workspace files the agent wrote, previewed
// beside the transcript. HTML runs in a sandboxed iframe (scripts allowed, no
// same-origin — the document is inert toward the console); markdown and SVG
// render in a script-less iframe, so agent-authored content can't escape into
// the page. Everything else shows as source.

const PALETTE = {
  light: { bg: "#ffffff", fg: "#141413", muted: "#6f6e69", border: "#e5e2d8", code: "#f0eee6" },
  dark: { bg: "#262624", fg: "#f5f4ee", muted: "#b7b5ad", border: "#3e3e3a", code: "#1f1e1d" },
};

function markdownDoc(markdown: string, dark: boolean): string {
  const c = dark ? PALETTE.dark : PALETTE.light;
  const body = marked.parse(markdown, { async: false }) as string;
  return `<!doctype html><meta charset="utf-8"><style>
    body { margin: 0; padding: 24px 28px; background: ${c.bg}; color: ${c.fg};
      font: 15px/1.65 -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif; }
    h1, h2, h3, h4 { line-height: 1.3; margin: 1.4em 0 0.5em; } h1:first-child { margin-top: 0; }
    a { color: inherit; } img, svg, video { max-width: 100%; }
    code { font: 0.85em ui-monospace, "SF Mono", Menlo, Consolas, monospace;
      background: ${c.code}; border-radius: 4px; padding: 0.1em 0.35em; }
    pre { background: ${c.code}; border-radius: 8px; padding: 12px 14px; overflow-x: auto; }
    pre code { background: none; padding: 0; }
    blockquote { margin: 0; padding-left: 14px; border-left: 3px solid ${c.border}; color: ${c.muted}; }
    table { border-collapse: collapse; } th, td { border: 1px solid ${c.border}; padding: 5px 10px; }
    hr { border: none; border-top: 1px solid ${c.border}; }
  </style><body>${body}</body>`;
}

function svgDoc(svg: string, dark: boolean): string {
  const c = dark ? PALETTE.dark : PALETTE.light;
  return `<!doctype html><meta charset="utf-8"><style>
    html, body { margin: 0; height: 100%; } body { display: grid; place-items: center;
      background: ${c.bg}; padding: 16px; box-sizing: border-box; }
    svg { max-width: 100%; max-height: 100%; }
  </style><body>${svg}</body>`;
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <Button
      variant="ghost"
      size="sm"
      title="Copy source"
      onClick={() => {
        // clipboard is undefined on insecure origins (console over plain http
        // on a LAN address); fail quietly rather than throwing in the handler
        navigator.clipboard?.writeText(text).then(
          () => {
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
          },
          () => {},
        );
      }}
    >
      {copied ? <Check /> : <Copy />}
    </Button>
  );
}

function download(name: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  // revoking synchronously can race the download the click just started
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

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
  const { resolvedTheme } = useTheme();
  // resolvedTheme is undefined until after hydration; rendering the iframe
  // before it settles mounts every preview twice (light flash, and an HTML
  // artifact's scripts run twice).
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  const dark = resolvedTheme === "dark";

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
    <aside className="flex w-full flex-col bg-card lg:w-[min(46%,660px)] lg:border-l lg:border-border">
      <div className="flex items-center gap-1.5 border-b border-border px-3 py-2">
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
        <Button variant="ghost" size="sm" title="Close" onClick={onClose}>
          <X />
        </Button>
      </div>

      {source ? (
        <pre className="min-h-0 flex-1 overflow-auto px-4 py-3 font-mono text-xs leading-relaxed whitespace-pre">
          {version.content}
        </pre>
      ) : !mounted ? (
        <div className="min-h-0 flex-1" />
      ) : (
        <iframe
          // remount when the document changes so stateful HTML restarts clean.
          // keyed on the version index, not seq — one assistant message can
          // carry two writes to the same file, which share a seq. `dark` is
          // only in the key for the kinds whose document embeds the palette;
          // an HTML artifact would otherwise reload (losing its state) on a
          // theme toggle that doesn't affect it.
          key={`${artifact.path}:${index}${artifact.kind === "html" ? "" : `:${dark}`}`}
          title={artifact.name}
          sandbox={artifact.kind === "html" ? "allow-scripts" : ""}
          srcDoc={
            artifact.kind === "html"
              ? version.content
              : artifact.kind === "svg"
                ? svgDoc(version.content, dark)
                : markdownDoc(version.content, dark)
          }
          className="min-h-0 flex-1 border-0 bg-white"
          style={dark && artifact.kind !== "html" ? { background: PALETTE.dark.bg } : undefined}
        />
      )}
    </aside>
  );
}
