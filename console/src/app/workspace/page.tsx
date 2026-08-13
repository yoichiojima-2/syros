"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { ArrowLeft, Download, FileText, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { CopyButton, download } from "@/components/artifact-viewer";
import { FileManager, type FileOps } from "@/components/file-manager";
import { useAction, useNow, useWorkspaceFiles, useWorkspaces } from "@/lib/hooks";
import { post } from "@/lib/api";
import { shortId } from "@/lib/format";
import type { BulkFilesResponse, OkResponse, StoredFile } from "@/lib/types";

// One shared workspace (workspaces/{name}/ in the bucket), editable. Master–
// detail like the artifacts page, but the right pane is a text editor rather
// than a preview: this is the only surface where a human can change what a
// session will see on its next restore, and the only one that can delete a
// file at all — checkpoint() never removes blobs, so a file dropped inside a
// run comes back until someone deletes it here.
//
// Every write is refused while a run holds the lease (409 from the API). The
// disabled controls below are courtesy; the server is the actual guard.

function fileUrl(workspace: string, file: string): string {
  return `/api/workspaces/${encodeURIComponent(workspace)}/file?name=${encodeURIComponent(file)}`;
}

/** Decoded-as-text bytes that clearly weren't text. Cheap and good enough to
 *  keep the editor from mangling a binary a session dropped in the workspace. */
function looksBinary(text: string): boolean {
  if (text.includes("\u0000")) return true;
  // response.text() decodes as UTF-8, so non-text bytes arrive as U+FFFD
  const replaced = (text.match(/\uFFFD/g) || []).length;
  return replaced > 4 && replaced > text.length / 100;
}

export default function WorkspacePage() {
  // useSearchParams requires a Suspense boundary under static export
  return (
    <Suspense>
      <WorkspaceInner />
    </Suspense>
  );
}

function WorkspaceInner() {
  const router = useRouter();
  const params = useSearchParams();
  const name = params.get("name");
  const file = params.get("file");
  const workspaces = useWorkspaces();
  const { files, refresh } = useWorkspaceFiles(name);
  const now = useNow();
  const [flash, run] = useAction();

  const summary = workspaces?.find((w) => w.name === name) ?? null;
  const busy = summary?.busy ?? false;
  const lockedBy = busy
    ? `a run holds this workspace (${shortId(summary?.lease_session_id || "unknown")})`
    : undefined;

  const select = (nextFile: string | null) => {
    if (!name) return;
    const query = new URLSearchParams({ name });
    if (nextFile) query.set("file", nextFile);
    router.replace(`/workspace?${query}`);
  };

  const base = name ? `/api/workspaces/${encodeURIComponent(name)}` : "";
  const ops = useMemo<FileOps>(
    () => ({
      write: (file, content, encoding = "utf-8") =>
        post<OkResponse>(`${base}/file`, { name: file, content, encoding }),
      removeMany: (names) => post<BulkFilesResponse>(`${base}/files/delete`, { names }),
      rename: (from, to) => post<OkResponse>(`${base}/file/rename`, { from, to }),
      setTags: (file, tags) => post<OkResponse>(`${base}/file/tags`, { name: file, tags }),
      deleteFolder: (folder) => post<OkResponse>(`${base}/folder/delete`, { folder }),
    }),
    [base],
  );

  if (!name) {
    return (
      <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-3 text-muted-foreground">
        <p className="text-[13px]">No workspace selected.</p>
        <Button variant="outline" size="sm" asChild>
          <Link href="/workspaces">
            <ArrowLeft /> All workspaces
          </Link>
        </Button>
      </div>
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
      <aside className="flex shrink-0 flex-col gap-3 overflow-y-auto border-b border-border p-4 lg:w-72 lg:border-r lg:border-b-0">
        <div>
          <Link
            href="/workspaces"
            className="flex items-center gap-1 px-1 text-[11px] text-muted-foreground hover:text-foreground"
          >
            <ArrowLeft className="size-3" /> Workspaces
          </Link>
          <h1 className="px-1 pt-1 font-mono text-lg font-semibold tracking-tight break-all">
            {name}
          </h1>
          <div className="flex items-center gap-1.5 px-1 pt-1.5">
            {busy ? (
              <>
                <Badge>
                  <span className="size-[7px] rounded-full bg-ok animate-pulse-dot" />
                  busy
                </Badge>
                {summary?.lease_session_id && (
                  <Link
                    href={`/session?sid=${summary.lease_session_id}`}
                    className="font-mono text-[11px] text-muted-foreground hover:text-foreground hover:underline"
                  >
                    {shortId(summary.lease_session_id)}
                  </Link>
                )}
              </>
            ) : (
              <Badge>
                <span className="size-[7px] rounded-full bg-faint" />
                free
              </Badge>
            )}
          </div>
          {busy && (
            <p className="px-1 pt-2 text-[11px] text-muted-foreground">
              Editing is off while a run holds the lease — its checkpoint would overwrite your
              changes when it finishes.
            </p>
          )}
        </div>

        {files === null ? (
          <div className="space-y-2">
            <Skeleton className="h-7" />
            <Skeleton className="h-7" />
          </div>
        ) : (
          <FileManager
            files={files}
            selected={file}
            now={now}
            disabled={busy}
            disabledReason={lockedBy}
            onSelect={select}
            onRenamed={(from, to) => {
              if (from === file) select(to);
            }}
            onMutated={refresh}
            run={run}
            ops={ops}
          />
        )}
        {flash && <p className="px-1 text-[11px] text-muted-foreground">{flash}</p>}
      </aside>

      <main className="flex min-h-0 min-w-0 flex-1 flex-col">
        {file ? (
          <FileEditor
            workspace={name}
            file={file}
            files={files}
            busy={busy}
            lockedBy={lockedBy}
            onChanged={refresh}
            onDeleted={() => select(null)}
          />
        ) : (
          <div className="flex flex-1 flex-col items-center justify-center gap-2 text-muted-foreground">
            <FileText className="size-6" />
            <p className="text-[13px]">Pick a file to edit it.</p>
          </div>
        )}
      </main>
    </div>
  );
}

function FileEditor({
  workspace,
  file,
  files,
  busy,
  lockedBy,
  onChanged,
  onDeleted,
}: {
  workspace: string;
  file: string;
  files: StoredFile[] | null;
  busy: boolean;
  lockedBy: string | undefined;
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
    fetch(fileUrl(workspace, file))
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
  }, [workspace, file, updated]);

  const dirty = saved !== null && draft !== saved;

  const save = () =>
    run(async () => {
      await post<OkResponse>(`/api/workspaces/${encodeURIComponent(workspace)}/file`, {
        name: file,
        content: draft,
      });
      setSaved(draft);
      onChanged();
      return "saved";
    });

  const remove = () => {
    if (!confirm(`Delete ${file}? It is removed from the bucket permanently.`)) return;
    run(async () => {
      await post<OkResponse>(`/api/workspaces/${encodeURIComponent(workspace)}/file/delete`, {
        name: file,
      });
      onChanged();
      onDeleted();
      return "deleted";
    });
  };

  return (
    <>
      <div className="flex items-center gap-1.5 border-b border-border px-3 py-2">
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
            else window.open(fileUrl(workspace, file), "_blank");
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
