// Browser-side upload primitives shared by the file manager and the skill
// upload flows. Every write goes out as base64 in a JSON body — the console
// server has no multipart path — so the whole job is turning whatever the
// browser hands us (a flat pick, a directory pick, a drop) into the same
// {path, file} list, with relative paths preserved.

export interface PickedFile {
  path: string;
  file: File;
}

/** Mirror of MAX_PREVIEW_BYTES in src/syros/console/objects.py — the server's
 *  per-file write cap. Checked here so one big file doesn't 413 midway through
 *  a batch, leaving half a directory uploaded.
 *
 *  Load-bearing: base64 inflates a body by 4/3, so this must stay comfortably
 *  under MAX_BODY_BYTES in src/syros/console/server.py (16 MiB) or an upload
 *  under the per-file cap still dies at the body cap — where the server closes
 *  the socket mid-request and the browser reports a network error, not a 413.
 *  10 MiB → ~13.3 MiB body, ~2.8 MiB of headroom. Raise both together. */
export const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;

/** Mirror of IGNORED in src/syros/skills.py: a skill folder usually sits in a
 *  checkout, and .git/ or .DS_Store is not part of the skill. */
export function ignored(path: string): boolean {
  return path
    .split("/")
    .some((part) => part.startsWith(".") || part === "__pycache__" || part === "node_modules");
}

/** `ignored` for the generic file manager, which is not building a skill: only
 *  tooling state that rode along inside a dropped *directory* is dropped, so
 *  deliberately picking a lone .env or .gitignore into a workspace still works. */
export function ignoredInTree(path: string): boolean {
  return path.includes("/") && ignored(path);
}

/** Split a pick into what to upload and what to drop — tooling state, and files
 *  over the server's per-write cap. Both upload flows partition the same way so
 *  a skipped file is reported rather than 413-ing midway through the batch. */
export function uploadable(
  picked: PickedFile[],
  isIgnored: (path: string) => boolean = ignored,
): { keep: PickedFile[]; dropped: PickedFile[] } {
  const keep: PickedFile[] = [];
  const dropped: PickedFile[] = [];
  for (const item of picked) {
    (isIgnored(item.path) || item.file.size > MAX_UPLOAD_BYTES ? dropped : keep).push(item);
  }
  return { keep, dropped };
}

export function readAsBase64(picked: File): Promise<string> {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    // readAsDataURL gives "data:<type>;base64,<payload>" — we want the payload
    reader.onload = () => resolve(String(reader.result).split(",")[1] ?? "");
    reader.onerror = () => reject(new Error(`could not read ${picked.name}`));
    reader.readAsDataURL(picked);
  });
}

/** Walk a dropped file or directory entry, preserving relative paths. */
export async function walkEntry(entry: FileSystemEntry, prefix: string): Promise<PickedFile[]> {
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

/** Everything in a drop, directories walked. Falls back to the flat file list
 *  when the entries API isn't available. */
export async function entriesFromDrop(e: React.DragEvent): Promise<PickedFile[]> {
  const entries = [...e.dataTransfer.items]
    .map((item) => item.webkitGetAsEntry?.())
    .filter((entry): entry is FileSystemEntry => !!entry);
  if (!entries.length) {
    return [...e.dataTransfer.files].map((file) => ({ path: file.name, file }));
  }
  const nested = await Promise.all(entries.map((entry) => walkEntry(entry, "")));
  return nested.flat();
}

/** Everything an <input type="file"> picked. A webkitdirectory input sets
 *  webkitRelativePath to "<folder>/<rest>", matching what a dropped folder
 *  yields, so both paths through the UI produce the same shape. */
export function filesFromInput(files: FileList | null): PickedFile[] {
  return [...(files ?? [])].map((file) => ({
    path: (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name,
    file,
  }));
}
