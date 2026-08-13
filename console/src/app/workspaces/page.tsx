"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ChevronDown, ChevronRight } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { StateBadge } from "@/components/state-badge";
import { useNow, useWorkspaces } from "@/lib/hooks";
import { api } from "@/lib/api";
import { bytes, relTime, shortId } from "@/lib/format";
import type { WorkspaceFilesResponse, StoredFile, WorkspaceSummary } from "@/lib/types";
import { cn } from "@/lib/utils";

export default function WorkspacesPage() {
  const workspaces = useWorkspaces();
  const now = useNow();
  const [expanded, setExpanded] = useState<string | null>(null);

  return (
    <div className="min-h-0 flex-1 space-y-5 overflow-y-auto p-6">
      <h1 className="font-serif text-2xl tracking-tight">Workspaces</h1>
      <Card>
        <CardContent className="px-2 py-2">
          {workspaces === null ? (
            <div className="space-y-2 p-2">
              <Skeleton className="h-8" />
              <Skeleton className="h-8" />
            </div>
          ) : workspaces.length === 0 ? (
            <p className="p-6 text-center text-[13px] text-muted-foreground">
              No shared workspaces yet — sessions created with{" "}
              <code className="font-mono text-xs">AgentOptions(workspace=&quot;name&quot;)</code>{" "}
              appear here.
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead />
                  <TableHead>Name</TableHead>
                  <TableHead>Lease</TableHead>
                  <TableHead>Sessions</TableHead>
                  <TableHead className="text-right">Files</TableHead>
                  <TableHead className="text-right">Size</TableHead>
                  <TableHead className="text-right">Updated</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {workspaces.map((workspace) => (
                  <WorkspaceRow
                    key={workspace.name}
                    workspace={workspace}
                    now={now}
                    expanded={expanded === workspace.name}
                    onToggle={() =>
                      setExpanded(expanded === workspace.name ? null : workspace.name)
                    }
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
}: {
  workspace: WorkspaceSummary;
  now: number;
  expanded: boolean;
  onToggle: () => void;
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
      <TableCell colSpan={6} className="py-2">
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
