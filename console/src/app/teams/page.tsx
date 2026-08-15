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
import { StateBadge } from "@/components/state-badge";
import { useAction, useNow, useTeams } from "@/lib/hooks";
import { api, post } from "@/lib/api";
import { bytes, relTime, shortId } from "@/lib/format";
import type { OkResponse, TeamFilesResponse, StoredFile, TeamSummary } from "@/lib/types";
import { cn } from "@/lib/utils";

export default function TeamsPage() {
  const router = useRouter();
  const teams = useTeams();
  const now = useNow();
  const [expanded, setExpanded] = useState<string | null>(null);
  const [flash, run] = useAction();

  const create = () => {
    const name = prompt("New team name (lowercase, [a-z0-9_-])");
    if (!name) return;
    run(async () => {
      await post<OkResponse>("/api/teams", { name });
      router.push(`/team?name=${encodeURIComponent(name)}`);
    });
  };

  const remove = (team: TeamSummary) => {
    if (
      !confirm(
        `Delete team ${team.name}? Its ${team.file_count} file${
          team.file_count === 1 ? "" : "s"
        } are removed from the bucket permanently.`,
      )
    )
      return;
    run(async () => {
      await post<OkResponse>(`/api/teams/${encodeURIComponent(team.name)}/delete`, {});
      return `deleted ${team.name}`;
    });
  };

  return (
    <div className="min-h-0 flex-1 space-y-5 overflow-y-auto p-4 sm:p-6">
      <div className="flex items-center justify-between">
        <h1 className="font-serif text-2xl tracking-tight">Teams</h1>
        <div className="flex items-center gap-3">
          {flash && <span className="text-[11px] text-muted-foreground">{flash}</span>}
          <Button variant="outline" size="sm" onClick={create}>
            <FolderPlus /> New team
          </Button>
        </div>
      </div>
      <Card>
        <CardContent className="px-2 py-2">
          {teams === null ? (
            <div className="space-y-2 p-2">
              <Skeleton className="h-8" />
              <Skeleton className="h-8" />
            </div>
          ) : teams.length === 0 ? (
            <p className="p-6 text-center text-[13px] text-muted-foreground">
              No teams yet — sessions created with{" "}
              <code className="font-mono text-xs">AgentOptions(team=&quot;name&quot;)</code>{" "}
              appear here.
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead />
                  <TableHead>Name</TableHead>
                  <TableHead>Description</TableHead>
                  <TableHead>Lease</TableHead>
                  <TableHead>Sessions</TableHead>
                  <TableHead className="text-right">Files</TableHead>
                  <TableHead className="text-right">Size</TableHead>
                  <TableHead className="text-right">Updated</TableHead>
                  <TableHead />
                </TableRow>
              </TableHeader>
              <TableBody>
                {teams.map((team) => (
                  <TeamRow
                    key={team.name}
                    team={team}
                    now={now}
                    expanded={expanded === team.name}
                    onToggle={() => setExpanded(expanded === team.name ? null : team.name)}
                    onDelete={() => remove(team)}
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

function TeamRow({
  team,
  now,
  expanded,
  onToggle,
  onDelete,
}: {
  team: TeamSummary;
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
            href={`/team?name=${encodeURIComponent(team.name)}`}
            onClick={(e) => e.stopPropagation()}
            className="hover:underline"
          >
            {team.name}
          </Link>
        </TableCell>
        <TableCell className="max-w-48 truncate text-xs text-muted-foreground">
          {team.description || "—"}
        </TableCell>
        <TableCell>
          {team.busy ? (
            <span className="flex items-center gap-1.5">
              <Badge>
                <span className="size-[7px] rounded-full bg-ok animate-pulse-dot" />
                busy
              </Badge>
              {team.lease_session_id && (
                <Link
                  href={`/session?sid=${team.lease_session_id}`}
                  onClick={(e) => e.stopPropagation()}
                  className="font-mono text-[11px] text-muted-foreground hover:text-foreground hover:underline"
                >
                  {shortId(team.lease_session_id)}
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
            {team.sessions.map((session) => (
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
            {team.sessions.length === 0 && (
              <span className="text-[11px] text-faint">—</span>
            )}
          </span>
        </TableCell>
        <TableCell className="text-right font-mono text-xs">{team.file_count}</TableCell>
        <TableCell className="text-right font-mono text-xs">
          {bytes(team.total_size)}
        </TableCell>
        <TableCell className="text-right text-xs text-muted-foreground">
          {relTime(team.updated, now)}
        </TableCell>
        <TableCell className="w-8 text-right">
          <Button
            variant="ghost"
            size="sm"
            disabled={team.busy}
            title={
              team.busy ? "a run holds this team's workspace" : `Delete ${team.name}`
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
      {expanded && <FilesRow name={team.name} now={now} />}
    </>
  );
}

function FilesRow({ name, now }: { name: string; now: number }) {
  const [files, setFiles] = useState<StoredFile[] | null>(null);
  useEffect(() => {
    let cancelled = false;
    api<TeamFilesResponse>(`/api/teams/${name}/files`)
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
      <TableCell colSpan={8} className="py-2">
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
