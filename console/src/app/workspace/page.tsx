"use client";

import { Suspense, useMemo } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { ArrowLeft, FilePlus2, FileText, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { FileEditor } from "@/components/file-editor";
import { FileManager, type FileOps } from "@/components/file-manager";
import { OptionsForm } from "@/components/options-form";
import { useAction, useNow, useSkills, useWorkspaceFiles, useWorkspaces } from "@/lib/hooks";
import { post } from "@/lib/api";
import { bytes, relTime, shortId } from "@/lib/format";
import type { BulkFilesResponse, OkResponse } from "@/lib/types";

// One workspace: a shared workspace (workspaces/{name}/ in the bucket) plus stored
// option defaults, a description and the workspace's own skills. The file editor is
// the only surface where a human can change what a session will see on its
// next restore, and the only one that can delete a file at all — checkpoint()
// never removes blobs, so a file dropped inside a run comes back until someone
// deletes it here.
//
// Every workspace write is refused while a run holds the lease (409 from the
// API). The disabled controls below are courtesy; the server is the actual guard.

const CLAUDE_MD = "CLAUDE.md";

function fileUrl(workspace: string, file: string): string {
  return `/api/workspaces/${encodeURIComponent(workspace)}/file?name=${encodeURIComponent(file)}`;
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

  const summary = workspaces?.find((t) => t.name === name) ?? null;
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

  const hasClaudeMd = files?.some((f) => f.name === CLAUDE_MD) ?? false;
  const editorProps = (editedFile: string) => ({
    file: editedFile,
    files,
    url: fileUrl(name, editedFile),
    busy,
    lockedBy,
    save: (content: string) =>
      post<OkResponse>(`${base}/file`, { name: editedFile, content }),
    remove: () => post<OkResponse>(`${base}/file/delete`, { name: editedFile }),
    onChanged: refresh,
  });

  return (
    <div className="min-h-0 flex-1 space-y-5 overflow-y-auto p-4 sm:p-6">
      <div>
        <Link
          href="/workspaces"
          className="flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="size-3" /> Workspaces
        </Link>
        <div className="flex flex-wrap items-center gap-2.5 pt-1">
          <h1 className="font-mono text-2xl font-semibold tracking-tight break-all">{name}</h1>
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
          {flash && <span className="text-[11px] text-muted-foreground">{flash}</span>}
        </div>
        {summary?.description && (
          <p className="pt-1 text-[13px] text-muted-foreground">{summary.description}</p>
        )}
        {busy && (
          <p className="pt-2 text-[11px] text-muted-foreground">
            Workspace editing is off while a run holds the lease — its checkpoint would overwrite
            your changes when it finishes.
          </p>
        )}
      </div>

      <Card>
        <CardHeader>
          <CardTitle>CLAUDE.md — how the agent works for this workspace</CardTitle>
          <CardDescription>
            An ordinary workspace file the agent reads at run start; edit it to shape how every
            session on this workspace behaves.
          </CardDescription>
        </CardHeader>
        <CardContent className="p-0">
          {files === null ? (
            <div className="p-4">
              <Skeleton className="h-24" />
            </div>
          ) : hasClaudeMd ? (
            <div className="flex h-[45svh] flex-col">
              <FileEditor {...editorProps(CLAUDE_MD)} onDeleted={refresh} />
            </div>
          ) : (
            <div className="flex flex-col items-center gap-3 p-6">
              <p className="text-[13px] text-muted-foreground">No CLAUDE.md yet.</p>
              <Button
                variant="outline"
                size="sm"
                disabled={busy}
                title={lockedBy}
                onClick={() =>
                  run(async () => {
                    await post<OkResponse>(`${base}/file`, { name: CLAUDE_MD, content: "" });
                    refresh();
                    return `created ${CLAUDE_MD}`;
                  })
                }
              >
                <FilePlus2 /> Create CLAUDE.md
              </Button>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Files</CardTitle>
          <CardDescription>
            The shared workspace every session on this workspace restores at start and checkpoints when
            it goes idle.
          </CardDescription>
        </CardHeader>
        <CardContent className="p-0">
          <div className="flex h-[60svh] flex-col lg:flex-row">
            <aside className="flex max-h-[45svh] shrink-0 flex-col gap-3 overflow-y-auto border-b border-border p-4 lg:max-h-none lg:w-72 lg:border-r lg:border-b-0">
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
            </aside>
            <main className="flex min-h-0 min-w-0 flex-1 flex-col">
              {file ? (
                <FileEditor {...editorProps(file)} onDeleted={() => select(null)} />
              ) : (
                <div className="flex flex-1 flex-col items-center justify-center gap-2 text-muted-foreground">
                  <FileText className="size-6" />
                  <p className="text-[13px]">Pick a file to edit it.</p>
                </div>
              )}
            </main>
          </div>
        </CardContent>
      </Card>

      <WorkspaceSkills workspace={name} now={now} />

      <Card>
        <CardHeader>
          <CardTitle>Workspace settings</CardTitle>
          <CardDescription>
            Stored option defaults every session on this workspace inherits — a run&apos;s explicit
            options override them field by field. Edits apply to future runs only.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {summary === null ? (
            <div className="space-y-2">
              <Skeleton className="h-8" />
              <Skeleton className="h-8" />
            </div>
          ) : (
            <OptionsForm
              // remount when a poll delivers fresh stored options
              key={JSON.stringify([summary.options, summary.description])}
              stored={summary.options}
              description={summary.description}
              showDescription
              submitLabel="Save workspace"
              onSave={async (options, description) => {
                await post<OkResponse>(`/api/workspaces/${encodeURIComponent(name)}/update`, {
                  options,
                  description,
                });
                return "saved";
              }}
            />
          )}
        </CardContent>
      </Card>
    </div>
  );
}

/** The workspace's own skills (skills scoped under the workspace in the bucket), with
 *  the same browse/edit/delete flows as the global skills pages. */
function WorkspaceSkills({ workspace, now }: { workspace: string; now: number }) {
  const router = useRouter();
  const skills = useSkills(workspace);
  const [flash, run] = useAction();

  const create = () => {
    const name = prompt("New skill name (lowercase, [a-z0-9_-])");
    if (!name) return;
    router.push(`/skill?name=${encodeURIComponent(name)}&workspace=${encodeURIComponent(workspace)}`);
  };

  const remove = (name: string) => {
    if (!confirm(`Delete the whole skill ${name}? Every file under it is removed.`)) return;
    run(async () => {
      await post(`/api/skills/${encodeURIComponent(name)}/delete`, { workspace });
      return `deleted ${name}`;
    });
  };

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-3">
          <div className="flex-1 space-y-1.5">
            <CardTitle>Workspace skills</CardTitle>
            <CardDescription>
              Mounted into this workspace&apos;s sessions at run start, alongside the global skills.
            </CardDescription>
          </div>
          {flash && <span className="text-[11px] text-muted-foreground">{flash}</span>}
          <Button variant="outline" size="sm" onClick={create}>
            <FilePlus2 /> New skill
          </Button>
        </div>
      </CardHeader>
      <CardContent className="px-2 pb-2">
        {skills === null ? (
          <div className="space-y-2 p-2">
            <Skeleton className="h-8" />
            <Skeleton className="h-8" />
          </div>
        ) : skills.length === 0 ? (
          <p className="p-4 text-center text-[13px] text-muted-foreground">
            No workspace skills yet — create one and add its SKILL.md.
          </p>
        ) : (
          <ul className="space-y-0.5">
            {skills.map((skill) => (
              <li key={skill.name} className="flex items-center gap-3 px-2">
                <Link
                  href={`/skill?name=${encodeURIComponent(skill.name)}&workspace=${encodeURIComponent(workspace)}`}
                  className="min-w-0 flex-1 truncate font-mono text-[13px] font-medium hover:underline"
                >
                  {skill.name}
                </Link>
                <span className="font-mono text-xs text-muted-foreground">
                  {skill.file_count} file{skill.file_count === 1 ? "" : "s"} ·{" "}
                  {bytes(skill.total_size)}
                </span>
                <span className="text-xs text-faint">{relTime(skill.updated, now)}</span>
                <Button
                  variant="ghost"
                  size="sm"
                  title={`Delete ${skill.name}`}
                  onClick={() => remove(skill.name)}
                >
                  <Trash2 />
                </Button>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
