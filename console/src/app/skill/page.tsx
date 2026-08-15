"use client";

import { Suspense, useRef } from "react";
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
import { cn } from "@/lib/utils";

// One skill (skills/{name}/ in the bucket, or a team's own set with ?team=),
// editable. Same master–detail editor as the team page, without the lease
// gating: skills are copied into each sandbox HOME at run start, so a console
// edit never races a live run's checkpoint — it simply applies from the next
// run onward.

function fileUrl(skill: string, file: string, team: string | null): string {
  const query = new URLSearchParams({ name: file });
  if (team) query.set("team", team);
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
  const team = params.get("team");
  const { files, refresh } = useSkillFiles(name, team);
  const now = useNow();
  const [flash, run] = useAction();
  const uploadRef = useRef<HTMLInputElement>(null);

  const select = (nextFile: string | null) => {
    if (!name) return;
    const query = new URLSearchParams({ name });
    if (team) query.set("team", team);
    if (nextFile) query.set("file", nextFile);
    router.replace(`/skill?${query}`);
  };

  const upload = (picked: File) => {
    if (!name) return;
    run(async () => {
      const content = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        // readAsDataURL gives "data:<type>;base64,<payload>" — we want the payload
        reader.onload = () => resolve(String(reader.result).split(",")[1] ?? "");
        reader.onerror = () => reject(new Error(`could not read ${picked.name}`));
        reader.readAsDataURL(picked);
      });
      await post<OkResponse>(`/api/skills/${encodeURIComponent(name)}/file`, {
        name: picked.name,
        content,
        encoding: "base64",
        ...(team ? { team } : {}),
      });
      refresh();
      select(picked.name);
      return `uploaded ${picked.name}`;
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
        ...(team ? { team } : {}),
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
            href={team ? `/team?name=${encodeURIComponent(team)}` : "/skills"}
            className="flex items-center gap-1 px-1 text-[11px] text-muted-foreground hover:text-foreground"
          >
            <ArrowLeft className="size-3" /> {team ? `Team ${team}` : "Skills"}
          </Link>
          <h1 className="px-1 pt-1 font-mono text-lg font-semibold tracking-tight break-all">
            {name}
          </h1>
          <p className="px-1 pt-2 text-[11px] text-muted-foreground">
            {team
              ? `Mounted into ${team}'s sessions at run start — edits apply from the next run.`
              : "Mounted into every session at run start — edits apply from the next run."}
          </p>
        </div>

        <div className="flex gap-1.5 px-1">
          <Button variant="outline" size="sm" onClick={create} className="flex-1">
            <FilePlus2 /> New
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => uploadRef.current?.click()}
            className="flex-1"
          >
            <Upload /> Upload
          </Button>
          <input
            ref={uploadRef}
            type="file"
            className="hidden"
            onChange={(e) => {
              const picked = e.target.files?.[0];
              // reset so re-picking the same file fires change again
              e.target.value = "";
              if (picked) upload(picked);
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
        {flash && <p className="px-1 text-[11px] text-muted-foreground">{flash}</p>}
      </aside>

      <main className="flex min-h-0 min-w-0 flex-1 flex-col">
        {file ? (
          <FileEditor
            file={file}
            files={files}
            url={fileUrl(name, file, team)}
            save={(content) =>
              post<OkResponse>(`/api/skills/${encodeURIComponent(name)}/file`, {
                name: file,
                content,
                ...(team ? { team } : {}),
              })
            }
            remove={() =>
              post<OkResponse>(`/api/skills/${encodeURIComponent(name)}/file/delete`, {
                name: file,
                ...(team ? { team } : {}),
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
