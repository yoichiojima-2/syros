"use client";

import { useMemo, useRef, useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  FilePlus2,
  Folder,
  FolderPlus,
  Pencil,
  Tag,
  Trash2,
  Upload,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { bytes, relTime } from "@/lib/format";
import type { BulkFilesResponse, StoredFile } from "@/lib/types";
import { cn } from "@/lib/utils";

// Shared file management for the team workspace editor and the artifacts page: a
// virtual folder tree over the flat GCS listing, plus create / upload (multi,
// drag-drop) / rename / tag / bulk-delete. GCS has no directories, so a
// folder exists as long as something lives under its prefix — "new folder"
// plants a hidden {folder}/.keep blob and the tree hides .keep entries.

export const KEEP = ".keep";

export interface FileOps {
  write: (name: string, content: string, encoding?: "utf-8" | "base64") => Promise<unknown>;
  removeMany: (names: string[]) => Promise<BulkFilesResponse>;
  rename: (from: string, to: string) => Promise<unknown>;
  setTags: (name: string, tags: string[]) => Promise<unknown>;
  deleteFolder: (folder: string) => Promise<unknown>;
}

interface TreeFolder {
  path: string;
  name: string;
  folders: TreeFolder[];
  files: StoredFile[];
}

function buildTree(files: StoredFile[]): TreeFolder {
  const root: TreeFolder = { path: "", name: "", folders: [], files: [] };
  const byPath = new Map<string, TreeFolder>([["", root]]);
  const folderAt = (path: string): TreeFolder => {
    const existing = byPath.get(path);
    if (existing) return existing;
    const slash = path.lastIndexOf("/");
    const parent = folderAt(slash < 0 ? "" : path.slice(0, slash));
    const node: TreeFolder = { path, name: path.slice(slash + 1), folders: [], files: [] };
    parent.folders.push(node);
    byPath.set(path, node);
    return node;
  };
  for (const file of files) {
    const slash = file.name.lastIndexOf("/");
    const folder = folderAt(slash < 0 ? "" : file.name.slice(0, slash));
    if (file.name.slice(slash + 1) !== KEEP) folder.files.push(file);
  }
  const sortNode = (node: TreeFolder) => {
    node.folders.sort((a, b) => a.name.localeCompare(b.name));
    node.files.sort((a, b) => a.name.localeCompare(b.name));
    node.folders.forEach(sortNode);
  };
  sortNode(root);
  return root;
}

function readAsBase64(picked: File): Promise<string> {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    // readAsDataURL gives "data:<type>;base64,<payload>" — we want the payload
    reader.onload = () => resolve(String(reader.result).split(",")[1] ?? "");
    reader.onerror = () => reject(new Error(`could not read ${picked.name}`));
    reader.readAsDataURL(picked);
  });
}

/** Walk a dropped file or directory entry, preserving relative paths. */
async function walkEntry(
  entry: FileSystemEntry,
  prefix: string,
): Promise<{ path: string; file: File }[]> {
  if (entry.isFile) {
    const file = await new Promise<File>((resolve, reject) =>
      (entry as FileSystemFileEntry).file(resolve, reject),
    );
    return [{ path: prefix + entry.name, file }];
  }
  const reader = (entry as FileSystemDirectoryEntry).createReader();
  const children: FileSystemEntry[] = [];
  // readEntries returns batches; keep reading until an empty one
  for (;;) {
    const batch = await new Promise<FileSystemEntry[]>((resolve, reject) =>
      reader.readEntries(resolve, reject),
    );
    if (!batch.length) break;
    children.push(...batch);
  }
  const nested = await Promise.all(
    children.map((child) => walkEntry(child, `${prefix}${entry.name}/`)),
  );
  return nested.flat();
}

export function FileManager({
  files,
  selected,
  now,
  disabled,
  disabledReason,
  onSelect,
  onRenamed,
  onMutated,
  run,
  ops,
}: {
  files: StoredFile[];
  selected: string | null;
  now: number;
  disabled: boolean;
  disabledReason?: string;
  onSelect: (name: string) => void;
  onRenamed?: (from: string, to: string) => void;
  onMutated: () => void;
  run: (fn: () => Promise<string | void>) => Promise<void>;
  ops: FileOps;
}) {
  const uploadRef = useRef<HTMLInputElement>(null);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [dragging, setDragging] = useState(false);
  const dragDepth = useRef(0);
  const tree = useMemo(() => buildTree(files), [files]);

  const upload = (picked: { path: string; file: File }[]) => {
    if (!picked.length) return;
    run(async () => {
      for (const { path, file } of picked) {
        await ops.write(path, await readAsBase64(file), "base64");
      }
      onMutated();
      if (picked.length === 1) onSelect(picked[0].path);
      return picked.length === 1 ? `uploaded ${picked[0].path}` : `uploaded ${picked.length} files`;
    });
  };

  const create = () => {
    const created = prompt("New file (path relative to the root)");
    if (!created) return;
    run(async () => {
      await ops.write(created, "");
      onMutated();
      onSelect(created);
      return `created ${created}`;
    });
  };

  const createFolder = () => {
    const folder = prompt("New folder (path relative to the root)");
    if (!folder) return;
    const clean = folder.replace(/^\/+|\/+$/g, "");
    if (!clean) return;
    run(async () => {
      await ops.write(`${clean}/${KEEP}`, "");
      onMutated();
      return `created ${clean}/`;
    });
  };

  const rename = (from: string) => {
    const to = prompt("New path", from);
    if (!to || to === from) return;
    run(async () => {
      await ops.rename(from, to);
      onMutated();
      onRenamed?.(from, to);
      return `renamed to ${to}`;
    });
  };

  const editTags = (file: StoredFile) => {
    const edited = prompt(
      "Tags (comma-separated, lowercase)",
      (file.tags ?? []).join(", "),
    );
    if (edited === null) return;
    const tags = edited
      .split(",")
      .map((t) => t.trim())
      .filter(Boolean);
    run(async () => {
      await ops.setTags(file.name, tags);
      onMutated();
      return tags.length ? `tagged ${tags.join(", ")}` : "tags cleared";
    });
  };

  const removeChecked = () => {
    const names = [...checked];
    if (!confirm(`Delete ${names.length} file${names.length === 1 ? "" : "s"}? Removed from the bucket permanently.`))
      return;
    run(async () => {
      const result = await ops.removeMany(names);
      setChecked(new Set());
      onMutated();
      return result.failed.length
        ? `deleted ${result.deleted.length}, failed ${result.failed.length}`
        : `deleted ${result.deleted.length}`;
    });
  };

  const removeFolder = (path: string) => {
    if (!confirm(`Delete folder ${path}/ and everything in it? Removed from the bucket permanently.`))
      return;
    run(async () => {
      await ops.deleteFolder(path);
      setChecked(new Set());
      onMutated();
      return `deleted ${path}/`;
    });
  };

  const toggleChecked = (name: string) =>
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });

  const toggleCollapsed = (path: string) =>
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    dragDepth.current = 0;
    setDragging(false);
    if (disabled) return;
    const items = [...e.dataTransfer.items];
    const entries = items
      .map((item) => item.webkitGetAsEntry?.())
      .filter((entry): entry is FileSystemEntry => !!entry);
    if (entries.length) {
      Promise.all(entries.map((entry) => walkEntry(entry, "")))
        .then((nested) => upload(nested.flat()))
        .catch(() => {});
      return;
    }
    upload([...e.dataTransfer.files].map((file) => ({ path: file.name, file })));
  };

  const renderFolder = (node: TreeFolder, depth: number): React.ReactNode => (
    <>
      {node.folders.map((child) => {
        const isCollapsed = collapsed.has(child.path);
        return (
          <li key={`d:${child.path}`}>
            <div
              className="group flex w-full items-center gap-1 rounded-lg px-2 py-1 text-left font-mono text-[12px] text-muted-foreground transition-colors hover:bg-secondary/70"
              style={{ paddingLeft: `${8 + depth * 14}px` }}
            >
              <button
                onClick={() => toggleCollapsed(child.path)}
                className="flex min-w-0 flex-1 items-center gap-1 hover:text-foreground"
                title={`${child.path}/`}
              >
                {isCollapsed ? (
                  <ChevronRight className="size-3 shrink-0" />
                ) : (
                  <ChevronDown className="size-3 shrink-0" />
                )}
                <Folder className="size-3.5 shrink-0" />
                <span className="min-w-0 truncate">{child.name}/</span>
              </button>
              <button
                title={disabledReason ?? `Delete ${child.path}/`}
                disabled={disabled}
                onClick={() => removeFolder(child.path)}
                className="hidden shrink-0 text-faint hover:text-destructive disabled:opacity-40 group-hover:block pointer-coarse:block"
              >
                <Trash2 className="size-3" />
              </button>
            </div>
            {!isCollapsed && <ul className="space-y-0.5">{renderFolder(child, depth + 1)}</ul>}
          </li>
        );
      })}
      {node.files.map((f) => (
        <li key={f.name}>
          <div
            className={cn(
              "group flex w-full items-center gap-1.5 rounded-lg px-2 py-1 text-left font-mono text-[12px] transition-colors",
              f.name === selected
                ? "bg-primary-soft font-medium"
                : "text-muted-foreground hover:bg-secondary/70 hover:text-foreground",
            )}
            style={{ paddingLeft: `${8 + depth * 14}px` }}
          >
            <Checkbox
              checked={checked.has(f.name)}
              onChange={() => toggleChecked(f.name)}
              className={cn(
                "size-3 shrink-0 group-hover:opacity-100 pointer-coarse:opacity-100",
                checked.size ? "opacity-100" : "opacity-0",
              )}
            />
            <button
              onClick={() => onSelect(f.name)}
              title={f.name}
              className="flex min-w-0 flex-1 items-baseline gap-1.5"
            >
              <span className="min-w-0 truncate">{f.name.split("/").pop()}</span>
              {(f.tags ?? []).map((tag) => (
                <span
                  key={tag}
                  className="shrink-0 rounded-full bg-secondary px-1.5 text-[9px] text-muted-foreground"
                >
                  {tag}
                </span>
              ))}
              <span className="ml-auto shrink-0 pl-1 text-[10px] text-faint">
                {relTime(f.updated, now) || bytes(f.size)}
              </span>
            </button>
            <span className="hidden shrink-0 items-center gap-1 group-hover:flex pointer-coarse:flex">
              <button
                title={disabledReason ?? "Rename / move"}
                disabled={disabled}
                onClick={() => rename(f.name)}
                className="text-faint hover:text-foreground disabled:opacity-40"
              >
                <Pencil className="size-3" />
              </button>
              <button
                title={disabledReason ?? "Edit tags"}
                disabled={disabled}
                onClick={() => editTags(f)}
                className="text-faint hover:text-foreground disabled:opacity-40"
              >
                <Tag className="size-3" />
              </button>
            </span>
          </div>
        </li>
      ))}
    </>
  );

  return (
    <div
      // shrink-0: these sit in scrollable asides — squashing (the flex
      // default) paints the list over whatever follows instead of scrolling
      className={cn("relative flex shrink-0 flex-col gap-2", dragging && "rounded-lg")}
      onDragEnter={(e) => {
        e.preventDefault();
        dragDepth.current += 1;
        if (!disabled) setDragging(true);
      }}
      onDragLeave={() => {
        dragDepth.current -= 1;
        if (dragDepth.current <= 0) setDragging(false);
      }}
      onDragOver={(e) => e.preventDefault()}
      onDrop={onDrop}
    >
      {dragging && (
        <div className="pointer-events-none absolute inset-0 z-10 grid place-items-center rounded-lg border-2 border-dashed border-primary bg-primary-soft/80">
          <p className="text-[12px] font-medium">Drop to upload</p>
        </div>
      )}
      <div className="flex gap-1.5 px-1">
        <Button
          variant="outline"
          size="sm"
          disabled={disabled}
          title={disabledReason ?? "New file"}
          onClick={create}
          className="flex-1"
        >
          <FilePlus2 /> New
        </Button>
        <Button
          variant="outline"
          size="sm"
          disabled={disabled}
          title={disabledReason ?? "New folder"}
          onClick={createFolder}
          className="flex-1"
        >
          <FolderPlus /> Folder
        </Button>
        <Button
          variant="outline"
          size="sm"
          disabled={disabled}
          title={disabledReason ?? "Upload files (or drag and drop)"}
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
            const picked = [...(e.target.files ?? [])];
            // reset so re-picking the same file fires change again
            e.target.value = "";
            upload(picked.map((file) => ({ path: file.name, file })));
          }}
        />
      </div>
      {checked.size > 0 && (
        <div className="flex items-center gap-2 px-1">
          <Button
            variant="destructive"
            size="sm"
            disabled={disabled}
            title={disabledReason}
            onClick={removeChecked}
            className="flex-1"
          >
            <Trash2 /> Delete {checked.size} selected
          </Button>
          <Button variant="ghost" size="sm" onClick={() => setChecked(new Set())}>
            Clear
          </Button>
        </div>
      )}
      <div>
        {files.length === 0 ? (
          <p className="px-1 text-[12px] text-muted-foreground">empty</p>
        ) : (
          <ul className="space-y-0.5">{renderFolder(tree, 0)}</ul>
        )}
      </div>
    </div>
  );
}
