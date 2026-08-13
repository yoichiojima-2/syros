"use client";

import { useEffect, useState } from "react";
import { useTheme } from "next-themes";
import { marked } from "marked";
import { Check, Copy } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { PreviewKind } from "@/lib/artifacts";

// Shared preview machinery: the sandboxed iframe rendering used by both the
// per-session artifact panel and the shared-artifacts page. HTML runs in a
// sandboxed iframe (scripts allowed, no same-origin — the document is inert
// toward the console); markdown and SVG render in a script-less iframe, so
// agent-authored content can't escape into the page.
//
// These documents stay on system font stacks rather than the console's Geist:
// without allow-same-origin the iframe has an opaque origin, so a webfont from
// the console's own origin is a cross-origin fetch the server sends no CORS
// header for, and inlining the woff2 as a data: URI into every srcdoc is not
// worth it here. Agent artifacts reading as plain documents is fine.

export const PALETTE = {
  light: { bg: "#ffffff", fg: "#18181b", muted: "#71717a", border: "#e4e4e7", code: "#f4f4f5" },
  dark: { bg: "#1b1b1e", fg: "#f4f4f5", muted: "#a1a1aa", border: "#2e2e33", code: "#141416" },
};

export function markdownDoc(markdown: string, dark: boolean): string {
  const c = dark ? PALETTE.dark : PALETTE.light;
  const body = marked.parse(markdown, { async: false }) as string;
  return `<!doctype html><meta charset="utf-8"><style>
    body { margin: 0; padding: 24px 28px; background: ${c.bg}; color: ${c.fg};
      font: 15px/1.65 -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif; }
    h1, h2, h3, h4 { line-height: 1.3; margin: 1.4em 0 0.5em; } h1:first-child { margin-top: 0; }
    a { color: inherit; } img, svg, video { max-width: 100%; }
    code { font: 0.85em ui-monospace, "SF Mono", SFMono-Regular, Menlo, Consolas, monospace;
      background: ${c.code}; border-radius: 4px; padding: 0.1em 0.35em; }
    pre { background: ${c.code}; border-radius: 8px; padding: 12px 14px; overflow-x: auto; }
    pre code { background: none; padding: 0; }
    blockquote { margin: 0; padding-left: 14px; border-left: 3px solid ${c.border}; color: ${c.muted}; }
    table { border-collapse: collapse; } th, td { border: 1px solid ${c.border}; padding: 5px 10px; }
    hr { border: none; border-top: 1px solid ${c.border}; }
  </style><body>${body}</body>`;
}

export function svgDoc(svg: string, dark: boolean): string {
  const c = dark ? PALETTE.dark : PALETTE.light;
  return `<!doctype html><meta charset="utf-8"><style>
    html, body { margin: 0; height: 100%; } body { display: grid; place-items: center;
      background: ${c.bg}; padding: 16px; box-sizing: border-box; }
    svg { max-width: 100%; max-height: 100%; }
  </style><body>${svg}</body>`;
}

export function CopyButton({ text }: { text: string }) {
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

export function download(name: string, text: string) {
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

export function ArtifactFrame({
  name,
  kind,
  content,
  frameKey,
}: {
  name: string;
  // callers show "code" as source themselves; anything else renders here
  kind: PreviewKind;
  content: string;
  frameKey: string;
}) {
  const { resolvedTheme } = useTheme();
  // resolvedTheme is undefined until after hydration; rendering the iframe
  // before it settles mounts every preview twice (light flash, and an HTML
  // artifact's scripts run twice).
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  const dark = resolvedTheme === "dark";

  if (!mounted) return <div className="min-h-0 flex-1" />;
  return (
    <iframe
      // remount when the document changes so stateful HTML restarts clean.
      // `dark` is only in the key for the kinds whose document embeds the
      // palette; an HTML artifact would otherwise reload (losing its state)
      // on a theme toggle that doesn't affect it.
      key={`${frameKey}${kind === "html" ? "" : `:${dark}`}`}
      title={name}
      sandbox={kind === "html" ? "allow-scripts" : ""}
      srcDoc={
        kind === "html" ? content : kind === "svg" ? svgDoc(content, dark) : markdownDoc(content, dark)
      }
      className="min-h-0 flex-1 border-0 bg-white"
      style={dark && kind !== "html" ? { background: PALETTE.dark.bg } : undefined}
    />
  );
}
