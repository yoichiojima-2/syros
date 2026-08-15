"use client";

import { Suspense, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { ArrowLeft, FilePlus2, FileText, Upload } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { FileEditor } from "@/components/file-editor";
import { useAction, useNow, useSkillFiles } from "@/lib/hooks";
import { post } from "@/lib/api";
import { bytes, relTime } from "@/lib/format";
import type { OkResponse } from "@/lib/types";
import {
  entriesFromDrop,
  filesFromInput,
  ignored,
  MAX_UPLOAD_BYTES,
  readAsBase64,
  type PickedFile,
} from "@/lib/upload";
import { cn } from "@/lib/utils";

// One skill (skills/{name}/ in the bucket, or a workspace's own set with ?workspace=),
// editable. Same master–detail editor as the workspace page, without the lease
// gating: skills are copied into each sandbox HOME at run start, so a console
// edit never races a live run's checkpoint — it simply applies from the next
// run onward.

function fileUrl(skill: string, file: string, workspace: string | null): string {
  const query = new URLSearchParams({ name: file });
  if (workspace) query.set("workspace", workspace);
  return `/api/skills/${encodeURIComponent(skill)}/file?${query}`;
}

export default function SkillPage() {
  // useSearchParams requires a Suspense boundary under static export
  return (
    <Suspense>
      <SkillInner />
    </Suspense>
  );
}

function SkillInner() {
  const router = useRouter();
  const params = useSearchParams();
  const name = params.get("name");
  const file = params.get("file");
  const workspace = params.get("workspace");
  const { files, refresh } = useSkillFiles(name, workspace);
  const now = useNow();
  const [flash, run] = useAction();
  const uploadRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const dragDepth = useRef(0);

  const select = (nextFile: string | null) => {
    if (!name) return;
    const query = new URLSearchParams({ name });
    if (workspace) query.set("workspace", workspace);
    if (nextFile) query.set("file", nextFile);
    router.replace(`/skill?${query}`);
  };

  // Files land at their relative path, so dropping a folder onto an open skill
  // adds it as a subdirectory of that skill. Same filters as a skill upload:
  // tooling state never belongs in a skill, and an oversized file would 413
  // midway through the batch.
  const upload = (all: PickedFile[]) => {
    if (!name || !all.length) return;
    const picked = all.filter((f) => !ignored(f.path) && f.file.size <= MAX_UPLOAD_BYTES);
    const dropped = all.length - picked.length;
    if (!picked.length) {
      run(async () => {
        throw new Error(`nothing to upload — ${dropped} file(s) skipped`);
      });
      return;
    }
    run(async () => {
      try {
        for (const { path, file } of picked) {
          await post<OkResponse>(`/api/skills/${encodeURIComponent(name)}/file`, {
            name: path,
            content: await readAsBase64(file),
            encoding: "base64",
            ...(workspace ? { workspace } : {}),
          });
        }
      } finally {
        refresh();
      }
      if (picked.length === 1) select(picked[0].path);
      const tail = dropped ? `, ${dropped} skipped` : "";
      return picked.length === 1
        ? `uploaded ${picked[0].path}${tail}`
        : `uploaded ${picked.length} files${tail}`;
    });
  };

  const create = () => {
    if (!name) return;
    const created = prompt("New file (path relative to the skill root, e.g. SKILL.md)");
    if (!created) return;
    run(async () => {
      await post<OkResponse>(`/api/skills/${encodeURIComponent(name)}/file`, {
        name: created,
        content: "",
        ...(workspace ? { workspace } : {}),
      });
      refresh();
      select(created);
      return `created ${created}`;
    });
  };

  if (!name) {
    return (
      <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-3 text-muted-foreground">
        <p className="text-[13px]">No skill selected.</p>
        <Button variant="outline" size="sm" asChild>
          <Link href="/skills">
            <ArrowLeft /> All skills
          </Link>
        </Button>
      </div>
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
      <aside className="flex max-h-[45svh] shrink-0 flex-col gap-3 overflow-y-auto border-b border-border p-4 lg:max-h-none lg:w-72 lg:border-r lg:border-b-0">
        <div>
          <Link
            href={workspace ? `/workspace?name=${encodeURIComponent(workspace)}` : "/skills"}
            className="flex items-center gap-1 px-1 text-[11px] text-muted-foreground hover:text-foreground"
          >
            <ArrowLeft className="size-3" /> {workspace ? `Workspace ${workspace}` : "Skills"}
          </Link>
          <h1 className="px-1 pt-1 font-mono text-lg font-semibold tracking-tight break-all">
            {name}
          </h1>
          <p className="px-1 pt-2 text-[11px] text-muted-foreground">
            {workspace
              ? `Mounted into ${workspace}'s sessions at run start — edits apply from the next run.`
              : "Mounted into every session at run start — edits apply from the next run."}
          </p>
        </div>

        <div
          className="relative flex flex-col gap-3"
          onDragEnter={(e) => {
            e.preventDefault();
            dragDepth.current += 1;
            setDragging(true);
          }}
          onDragLeave={() => {
            dragDepth.current -= 1;
            if (dragDepth.current <= 0) setDragging(false);
          }}
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => {
            e.preventDefault();
            dragDepth.current = 0;
            setDragging(false);
            entriesFromDrop(e)
              .then(upload)
              .catch(() => {});
          }}
        >
          {dragging && (
            <div className="pointer-events-none absolute inset-0 z-10 grid place-items-center rounded-lg border-2 border-dashed border-primary bg-primary-soft/80">
              <p className="text-[12px] font-medium">Drop to upload</p>
            </div>
          )}
          <div className="flex gap-1.5 px-1">
            <Button variant="outline" size="sm" onClick={create} className="flex-1">
              <FilePlus2 /> New
            </Button>
            <Button
              variant="outline"
              size="sm"
              title="Upload files (or drag and drop a file or folder)"
              onClick={() => uploadRef.current?.click()}
              className="flex-1"
            >
              <Upload /> Upload
            </Button>
            <input
              ref={uploadRef}
              type="file"
              multiple
              className="hidden"
              onChange={(e) => {
                const picked = filesFromInput(e.target.files);
                // reset so re-picking the same file fires change again
                e.target.value = "";
                upload(picked);
              }}
            />
          </div>

          <div className="min-h-0">
            {files === null ? (
              <div className="space-y-2">
                <Skeleton className="h-7" />
                <Skeleton className="h-7" />
              </div>
            ) : files.length === 0 ? (
              <p className="px-1 text-[12px] text-muted-foreground">empty</p>
            ) : (
              <ul className="space-y-0.5">
                {files.map((f) => (
                  <li key={f.name}>
                    <button
                      onClick={() => select(f.name)}
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
        </div>
        {flash && <p className="px-1 text-[11px] text-muted-foreground">{flash}</p>}
      </aside>

      <main className="flex min-h-0 min-w-0 flex-1 flex-col">
        {file ? (
          <FileEditor
            file={file}
            files={files}
            url={fileUrl(name, file, workspace)}
            save={(content) =>
              post<OkResponse>(`/api/skills/${encodeURIComponent(name)}/file`, {
                name: file,
                content,
                ...(workspace ? { workspace } : {}),
              })
            }
            remove={() =>
              post<OkResponse>(`/api/skills/${encodeURIComponent(name)}/file/delete`, {
                name: file,
                ...(workspace ? { workspace } : {}),
              })
            }
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
