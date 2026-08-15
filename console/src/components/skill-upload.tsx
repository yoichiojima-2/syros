"use client";

import { useRef, useState } from "react";
import { FolderUp } from "lucide-react";
import { Button } from "@/components/ui/button";
import { post } from "@/lib/api";
import type { OkResponse } from "@/lib/types";
import {
  entriesFromDrop,
  filesFromInput,
  ignored,
  MAX_UPLOAD_BYTES,
  readAsBase64,
  type PickedFile,
} from "@/lib/upload";
import { bytes } from "@/lib/format";
import { cn } from "@/lib/utils";

// Uploading a directory is the main way to create a skill: a skill *is* a
// directory (SKILL.md plus resources), and the API materialises one as soon as
// a blob lands under its prefix, so no create endpoint is involved — the
// folder name becomes the skill name and each file is one write. Mirror of
// `syros skills push`, which applies the same name/SKILL.md/ignore rules
// server-side (src/syros/skills.py).

/** Mirror of NAME in src/syros/names.py — validated here so a bad folder name
 *  fails before the first upload rather than midway through one. */
const SKILL_NAME = /^[a-z0-9][a-z0-9_-]{0,63}$/;

/** Group a picked/dropped tree by top-level folder and upload each as a skill.
 *  Every group is validated before anything is written, and oversized files are
 *  skipped and reported rather than 413-ing mid-batch — same as `skills push`. */
export async function uploadSkillFolders(picked: PickedFile[]): Promise<string> {
  const groups = new Map<string, PickedFile[]>();
  const skipped: string[] = [];
  for (const item of picked) {
    if (ignored(item.path)) continue;
    const slash = item.path.indexOf("/");
    if (slash < 0) continue; // a loose file carries no skill name
    if (item.file.size > MAX_UPLOAD_BYTES) {
      skipped.push(item.path);
      continue;
    }
    const name = item.path.slice(0, slash);
    const group = groups.get(name) ?? [];
    group.push({ path: item.path.slice(slash + 1), file: item.file });
    groups.set(name, group);
  }
  if (!groups.size) throw new Error("drop a skill directory — a folder with a SKILL.md in it");
  for (const [name, files] of groups) {
    if (!SKILL_NAME.test(name))
      throw new Error(`${name} is not a valid skill name ([a-z0-9][a-z0-9_-]*, max 64 chars)`);
    if (!files.some((f) => f.path === "SKILL.md"))
      throw new Error(`${name}/ has no SKILL.md — a skill directory must carry one`);
  }
  let count = 0;
  for (const [name, files] of groups) {
    for (const { path, file } of files) {
      await post<OkResponse>(`/api/skills/${encodeURIComponent(name)}/file`, {
        name: path,
        content: await readAsBase64(file),
        encoding: "base64",
      });
      count += 1;
    }
  }
  const names = [...groups.keys()];
  const files = `${count} file${count === 1 ? "" : "s"}`;
  const tail = skipped.length ? `, ${skipped.length} skipped (over ${bytes(MAX_UPLOAD_BYTES)})` : "";
  return names.length === 1
    ? `uploaded skill ${names[0]} (${files})${tail}`
    : `uploaded ${names.length} skills (${files})${tail}`;
}

interface UploadProps {
  onUploaded: () => void;
  run: (fn: () => Promise<string | void>) => Promise<void>;
}

/** "Upload skill" button over a hidden directory picker. */
export function SkillUpload({ onUploaded, run }: UploadProps) {
  const pickRef = useRef<HTMLInputElement>(null);

  return (
    <>
      <Button
        variant="outline"
        size="sm"
        title="Upload a skill directory (or drag one onto the list)"
        onClick={() => pickRef.current?.click()}
      >
        <FolderUp /> Upload skill
      </Button>
      <input
        ref={pickRef}
        type="file"
        multiple
        // not in React's JSX types; the attribute is what makes it a folder picker
        {...{ webkitdirectory: "" }}
        className="hidden"
        onChange={(e) => {
          const picked = filesFromInput(e.target.files);
          // reset so re-picking the same folder fires change again
          e.target.value = "";
          // refresh either way: a failure partway through still wrote files
          run(async () => {
            try {
              return await uploadSkillFolders(picked);
            } finally {
              onUploaded();
            }
          });
        }}
      />
    </>
  );
}

/** Wraps a skills list so a folder can be dropped straight onto it. */
export function SkillDropZone({
  onUploaded,
  run,
  className,
  children,
}: UploadProps & { className?: string; children: React.ReactNode }) {
  const [dragging, setDragging] = useState(false);
  const dragDepth = useRef(0);

  return (
    <div
      className={cn("relative", className)}
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
        // entriesFromDrop reads dataTransfer synchronously before it awaits
        const picked = entriesFromDrop(e);
        // refresh either way: a failure partway through still wrote files
        run(async () => {
          try {
            return await uploadSkillFolders(await picked);
          } finally {
            onUploaded();
          }
        });
      }}
    >
      {dragging && (
        <div className="pointer-events-none absolute inset-0 z-10 grid place-items-center rounded-lg border-2 border-dashed border-primary bg-primary-soft/80">
          <p className="text-[12px] font-medium">Drop a skill directory to upload</p>
        </div>
      )}
      {children}
    </div>
  );
}
