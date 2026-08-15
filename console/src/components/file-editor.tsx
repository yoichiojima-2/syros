"use client";

import { useEffect, useState } from "react";
import { Download, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { CopyButton, download } from "@/components/artifact-viewer";
import { useAction } from "@/lib/hooks";
import type { StoredFile } from "@/lib/types";

// Shared bucket-file text editor for the team workspace and skill pages: the
// page supplies the raw-bytes GET url and the save/delete calls, this owns the
// saved/draft state, the binary sniff and the toolbar. Pages that gate writes
// behind a lease pass busy/lockedBy; the server is the actual guard.

/** Decoded-as-text bytes that clearly weren't text. Cheap and good enough to
 *  keep the editor from mangling a binary a session dropped in the bucket. */
export function looksBinary(text: string): boolean {
  if (text.includes("\u0000")) return true;
  // response.text() decodes as UTF-8, so non-text bytes arrive as U+FFFD
  const replaced = (text.match(/\uFFFD/g) || []).length;
  return replaced > 4 && replaced > text.length / 100;
}

export function FileEditor({
  file,
  files,
  url,
  busy = false,
  lockedBy,
  save: saveFile,
  remove: removeFile,
  onChanged,
  onDeleted,
}: {
  file: string;
  files: StoredFile[] | null;
  url: string; // GET endpoint serving the raw bytes
  busy?: boolean; // a run holds the lease: editing is off
  lockedBy?: string;
  save: (content: string) => Promise<unknown>;
  remove: () => Promise<unknown>;
  onChanged: () => void;
  onDeleted: () => void;
}) {
  // `saved` is the last content known to be in the bucket; `draft` is the
  // textarea. Their difference is the dirty flag, so a save is just the two
  // converging — no separate "unsaved" bookkeeping to fall out of sync.
  const [saved, setSaved] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [binary, setBinary] = useState(false);
  const [flash, run] = useAction();
  const updated = files?.find((f) => f.name === file)?.updated ?? null;

  useEffect(() => {
    setSaved(null);
    setDraft("");
    setError(null);
    setBinary(false);
    let cancelled = false;
    fetch(url)
      .then(async (response) => {
        if (response.status === 413) throw new Error("too large to edit — download instead");
        if (!response.ok) {
          const body = await response.json().catch(() => null);
          throw new Error(body?.error || `HTTP ${response.status}`);
        }
        return response.text();
      })
      .then((text) => {
        if (cancelled) return;
        if (looksBinary(text)) {
          setBinary(true);
          return;
        }
        setSaved(text);
        setDraft(text);
      })
      .catch((err: Error) => {
        if (!cancelled) setError(err.message);
      });
    return () => {
      cancelled = true;
    };
    // refetch when the blob changes upstream, not on every poll
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url, updated]);

  const dirty = saved !== null && draft !== saved;

  const save = () =>
    run(async () => {
      await saveFile(draft);
      setSaved(draft);
      onChanged();
      return "saved";
    });

  const remove = () => {
    if (!confirm(`Delete ${file}? It is removed from the bucket permanently.`)) return;
    run(async () => {
      await removeFile();
      onChanged();
      onDeleted();
      return "deleted";
    });
  };

  return (
    <>
      <div className="flex flex-wrap items-center gap-x-1.5 gap-y-1 border-b border-border px-3 py-2">
        <span
          className="min-w-0 flex-1 truncate px-1.5 font-mono text-[13px] font-medium"
          title={file}
        >
          {file}
          {dirty && <span className="pl-1.5 text-[11px] text-muted-foreground">• unsaved</span>}
        </span>
        {flash && <span className="px-1 text-[11px] text-muted-foreground">{flash}</span>}
        {saved !== null && <CopyButton text={draft} />}
        <Button
          variant="ghost"
          size="sm"
          title="Download"
          onClick={() => {
            if (saved !== null) download(file.split("/").pop() || file, draft);
            else window.open(url, "_blank");
          }}
        >
          <Download />
        </Button>
        <Button variant="ghost" size="sm" title={lockedBy ?? "Delete"} disabled={busy} onClick={remove}>
          <Trash2 />
        </Button>
        {saved !== null && (
          <>
            <Button variant="outline" size="sm" disabled={!dirty} onClick={() => setDraft(saved)}>
              Revert
            </Button>
            <Button size="sm" disabled={busy || !dirty} title={lockedBy} onClick={save}>
              Save
            </Button>
          </>
        )}
      </div>
      {error ? (
        <p className="p-6 text-center text-[13px] text-muted-foreground">{error}</p>
      ) : binary ? (
        <p className="p-6 text-center text-[13px] text-muted-foreground">
          Not a text file — download it to inspect, or replace it with an upload.
        </p>
      ) : saved === null ? (
        <div className="flex-1 p-4">
          <Skeleton className="h-24" />
        </div>
      ) : (
        <Textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          disabled={busy}
          spellCheck={false}
          className="min-h-0 flex-1 resize-none overflow-auto px-4 py-3 font-mono text-xs leading-relaxed"
        />
      )}
    </>
  );
}
