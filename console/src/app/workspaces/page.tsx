"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ChevronDown, ChevronRight, FolderPlus, Trash2 } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { InstallPresetsButton } from "@/components/install-presets-button";
import { StateBadge } from "@/components/state-badge";
import { useAction, useNow, useWorkspaces } from "@/lib/hooks";
import { api, post } from "@/lib/api";
import { bytes, relTime, shortId } from "@/lib/format";
import type { OkResponse, WorkspaceFilesResponse, StoredFile, WorkspaceSummary } from "@/lib/types";
import { cn } from "@/lib/utils";

export default function WorkspacesPage() {
  const router = useRouter();
  const workspaces = useWorkspaces();
  const now = useNow();
  const [expanded, setExpanded] = useState<string | null>(null);
  const [flash, run] = useAction();

  const create = () => {
    const name = prompt("New workspace name (lowercase, [a-z0-9_-])");
    if (!name) return;
    run(async () => {
      await post<OkResponse>("/api/workspaces", { name });
      router.push(`/workspace?name=${encodeURIComponent(name)}`);
    });
  };

  const remove = (workspace: WorkspaceSummary) => {
    if (
      !confirm(
        `Delete workspace ${workspace.name}? Its ${workspace.file_count} file${
          workspace.file_count === 1 ? "" : "s"
        } are removed from the bucket permanently.`,
      )
    )
      return;
    run(async () => {
      await post<OkResponse>(`/api/workspaces/${encodeURIComponent(workspace.name)}/delete`, {});
      return `deleted ${workspace.name}`;
    });
  };

  return (
    <div className="min-h-0 flex-1 space-y-5 overflow-y-auto p-4 sm:p-6">
      <div className="flex items-center justify-between">
        <h1 className="font-serif text-2xl tracking-tight">Workspaces</h1>
        <div className="flex items-center gap-3">
          {flash && <span className="text-[11px] text-muted-foreground">{flash}</span>}
          <Button variant="outline" size="sm" onClick={create}>
            <FolderPlus /> New workspace
          </Button>
        </div>
      </div>
      <Card>
        <CardContent className="px-2 py-2">
          {workspaces === null ? (
            <div className="space-y-2 p-2">
              <Skeleton className="h-8" />
              <Skeleton className="h-8" />
            </div>
          ) : workspaces.length === 0 ? (
            <div className="space-y-4 p-6 text-center text-[13px] text-muted-foreground">
              <p>
                No workspaces yet — sessions created with{" "}
                <code className="font-mono text-xs">AgentOptions(workspace=&quot;name&quot;)</code>{" "}
                appear here.
              </p>
              <div>
                <InstallPresetsButton />
                <p className="mt-2 text-[11px]">
                  Adds a &quot;research&quot; workspace with a CLAUDE.md that loads as project
                  memory, and the agents that make it their own.
                </p>
              </div>
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead />
                  <TableHead>Name</TableHead>
                  <TableHead>Description</TableHead>
                  <TableHead>Members</TableHead>
                  <TableHead>Lease</TableHead>
                  <TableHead>Sessions</TableHead>
                  <TableHead className="text-right">Files</TableHead>
                  <TableHead className="text-right">Size</TableHead>
                  <TableHead className="text-right">Updated</TableHead>
                  <TableHead />
                </TableRow>
              </TableHeader>
              <TableBody>
                {workspaces.map((workspace) => (
                  <WorkspaceRow
                    key={workspace.name}
                    workspace={workspace}
                    now={now}
                    expanded={expanded === workspace.name}
                    onToggle={() => setExpanded(expanded === workspace.name ? null : workspace.name)}
                    onDelete={() => remove(workspace)}
                  />
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function WorkspaceRow({
  workspace,
  now,
  expanded,
  onToggle,
  onDelete,
}: {
  workspace: WorkspaceSummary;
  now: number;
  expanded: boolean;
  onToggle: () => void;
  onDelete: () => void;
}) {
  const Chevron = expanded ? ChevronDown : ChevronRight;
  return (
    <>
      <TableRow onClick={onToggle} className="cursor-pointer">
        <TableCell className="w-8">
          <Chevron className="size-4 text-muted-foreground" />
        </TableCell>
        <TableCell className="font-mono text-[13px] font-medium">
          <Link
            href={`/workspace?name=${encodeURIComponent(workspace.name)}`}
            onClick={(e) => e.stopPropagation()}
            className="hover:underline"
          >
            {workspace.name}
          </Link>
        </TableCell>
        <TableCell className="max-w-48 truncate text-xs text-muted-foreground">
          {workspace.description || "—"}
        </TableCell>
        <TableCell>
          {workspace.members.length === 0 ? (
            <span className="text-[11px] text-faint">—</span>
          ) : (
            <span className="flex flex-wrap items-center gap-1">
              {workspace.members.map((member) => (
                <Badge key={member} className="font-mono text-[11px]">
                  {member}
                </Badge>
              ))}
            </span>
          )}
        </TableCell>
        <TableCell>
          {workspace.busy ? (
            <span className="flex items-center gap-1.5">
              <Badge>
                <span className="size-[7px] rounded-full bg-ok animate-pulse-dot" />
                busy
              </Badge>
              {workspace.lease_session_id && (
                <Link
                  href={`/session?sid=${workspace.lease_session_id}`}
                  onClick={(e) => e.stopPropagation()}
                  className="font-mono text-[11px] text-muted-foreground hover:text-foreground hover:underline"
                >
                  {shortId(workspace.lease_session_id)}
                </Link>
              )}
            </span>
          ) : (
            <Badge>
              <span className="size-[7px] rounded-full bg-faint" />
              free
            </Badge>
          )}
        </TableCell>
        <TableCell>
          <span className="flex flex-wrap items-center gap-1.5">
            {workspace.sessions.map((session) => (
              <Link
                key={session.id}
                href={`/session?sid=${session.id}`}
                onClick={(e) => e.stopPropagation()}
                title={session.id}
                className="hover:opacity-80"
              >
                <StateBadge state={session.state} />
              </Link>
            ))}
            {workspace.sessions.length === 0 && (
              <span className="text-[11px] text-faint">—</span>
            )}
          </span>
        </TableCell>
        <TableCell className="text-right font-mono text-xs">{workspace.file_count}</TableCell>
        <TableCell className="text-right font-mono text-xs">
          {bytes(workspace.total_size)}
        </TableCell>
        <TableCell className="text-right text-xs text-muted-foreground">
          {relTime(workspace.updated, now)}
        </TableCell>
        <TableCell className="w-8 text-right">
          <Button
            variant="ghost"
            size="sm"
            disabled={workspace.busy}
            title={
              workspace.busy ? "a run holds this workspace" : `Delete ${workspace.name}`
            }
            onClick={(e) => {
              e.stopPropagation();
              onDelete();
            }}
          >
            <Trash2 />
          </Button>
        </TableCell>
      </TableRow>
      {expanded && <FilesRow name={workspace.name} now={now} />}
    </>
  );
}

function FilesRow({ name, now }: { name: string; now: number }) {
  const [files, setFiles] = useState<StoredFile[] | null>(null);
  useEffect(() => {
    let cancelled = false;
    api<WorkspaceFilesResponse>(`/api/workspaces/${name}/files`)
      .then((data) => {
        if (!cancelled) setFiles(data.files);
      })
      .catch(() => {
        if (!cancelled) setFiles([]);
      });
    return () => {
      cancelled = true;
    };
  }, [name]);

  return (
    <TableRow className="hover:bg-transparent">
      <TableCell />
      <TableCell colSpan={9} className="py-2">
        {files === null ? (
          <Skeleton className="h-5 w-48" />
        ) : files.length === 0 ? (
          <span className="text-[12px] text-muted-foreground">empty</span>
        ) : (
          <ul className="space-y-0.5">
            {files.map((file) => (
              <li
                key={file.name}
                className={cn("flex items-baseline gap-3 font-mono text-[12px]")}
              >
                <span className="min-w-0 truncate">{file.name}</span>
                <span className="text-muted-foreground">{bytes(file.size)}</span>
                <span className="text-faint">{relTime(file.updated, now)}</span>
              </li>
            ))}
          </ul>
        )}
      </TableCell>
    </TableRow>
  );
}
