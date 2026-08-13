"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { ArrowLeft, Download, FilePlus2, FileText, Trash2, Upload } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { CopyButton, download } from "@/components/artifact-viewer";
import { useAction, useNow, useSkillFiles } from "@/lib/hooks";
import { post } from "@/lib/api";
import { bytes, relTime } from "@/lib/format";
import type { OkResponse, StoredFile } from "@/lib/types";
import { cn } from "@/lib/utils";

// One skill (skills/{name}/ in the bucket), editable. Same master–detail
// editor as the workspace page, without the lease gating: skills are copied
// into each sandbox HOME at run start, so a console edit never races a live
// run's checkpoint — it simply applies from the next run onward.

function fileUrl(skill: string, file: string): string {
  return `/api/skills/${encodeURIComponent(skill)}/file?name=${encodeURIComponent(file)}`;
}

/** Decoded-as-text bytes that clearly weren't text. Cheap and good enough to
 *  keep the editor from mangling a binary resource bundled with a skill. */
function looksBinary(text: string): boolean {
  if (text.includes("\u0000")) return true;
  // response.text() decodes as UTF-8, so non-text bytes arrive as U+FFFD
  const replaced = (text.match(/\uFFFD/g) || []).length;
  return replaced > 4 && replaced > text.length / 100;
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
  const { files, refresh } = useSkillFiles(name);
  const now = useNow();
  const [flash, run] = useAction();
  const uploadRef = useRef<HTMLInputElement>(null);

  const select = (nextFile: string | null) => {
    if (!name) return;
    const query = new URLSearchParams({ name });
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
      <aside className="flex shrink-0 flex-col gap-3 overflow-y-auto border-b border-border p-4 lg:w-72 lg:border-r lg:border-b-0">
        <div>
          <Link
            href="/skills"
            className="flex items-center gap-1 px-1 text-[11px] text-muted-foreground hover:text-foreground"
          >
            <ArrowLeft className="size-3" /> Skills
          </Link>
          <h1 className="px-1 pt-1 font-mono text-lg font-semibold tracking-tight break-all">
            {name}
          </h1>
          <p className="px-1 pt-2 text-[11px] text-muted-foreground">
            Mounted into every session at run start — edits apply from the next run.
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
            skill={name}
            file={file}
            files={files}
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
  skill,
  file,
  files,
  onChanged,
  onDeleted,
}: {
  skill: string;
  file: string;
  files: StoredFile[] | null;
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
    fetch(fileUrl(skill, file))
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
  }, [skill, file, updated]);

  const dirty = saved !== null && draft !== saved;

  const save = () =>
    run(async () => {
      await post<OkResponse>(`/api/skills/${encodeURIComponent(skill)}/file`, {
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
      await post<OkResponse>(`/api/skills/${encodeURIComponent(skill)}/file/delete`, {
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
            else window.open(fileUrl(skill, file), "_blank");
          }}
        >
          <Download />
        </Button>
        <Button variant="ghost" size="sm" title="Delete" onClick={remove}>
          <Trash2 />
        </Button>
        {saved !== null && (
          <>
            <Button variant="outline" size="sm" disabled={!dirty} onClick={() => setDraft(saved)}>
              Revert
            </Button>
            <Button size="sm" disabled={!dirty} onClick={save}>
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
          spellCheck={false}
          className="min-h-0 flex-1 resize-none overflow-auto px-4 py-3 font-mono text-xs leading-relaxed"
        />
      )}
    </>
  );
}
