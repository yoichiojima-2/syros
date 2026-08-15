"use client";

import { useState } from "react";
import Link from "next/link";
import { ChevronDown, ChevronRight, DownloadCloud, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { SkillDropZone, SkillUpload } from "@/components/skill-upload";
import { useAction, useNow, useSkillFiles, useSkills } from "@/lib/hooks";
import { post } from "@/lib/api";
import { bytes, relTime } from "@/lib/format";
import type { SkillSummary, SyncSkillsResponse } from "@/lib/types";
import { cn } from "@/lib/utils";

// Skills live under skills/{name}/ in the bucket and are mounted into every
// session's HOME at run start, so what this page shows is exactly what the
// next run will discover. A skill is a directory, so uploading one is how you
// create one — drop a folder anywhere on the list, or use the picker. "Sync
// official skills" seeds the prefix with editable copies of
// github.com/anthropics/skills.

export default function SkillsPage() {
  const { skills, refresh } = useSkills();
  const now = useNow();
  const [expanded, setExpanded] = useState<string | null>(null);
  const [flash, run] = useAction();

  const sync = () =>
    run(async () => {
      const result = await post<SyncSkillsResponse>("/api/skills/sync");
      const skipped = result.skipped.length ? `, ${result.skipped.length} skipped (too large)` : "";
      refresh();
      return `synced ${result.files} file(s) across ${result.skills.length} skill(s)${skipped}`;
    });

  return (
    <div className="min-h-0 flex-1 space-y-5 overflow-y-auto p-4 sm:p-6">
      <div className="flex items-center gap-3">
        <h1 className="flex-1 font-serif text-2xl tracking-tight">Skills</h1>
        {flash && <span className="text-[11px] text-muted-foreground">{flash}</span>}
        <SkillUpload onUploaded={refresh} run={run} />
        <Button variant="outline" size="sm" onClick={sync}>
          <DownloadCloud /> Sync official skills
        </Button>
      </div>
      <SkillDropZone onUploaded={refresh} run={run}>
        <Card>
          <CardContent className="px-2 py-2">
            {skills === null ? (
              <div className="space-y-2 p-2">
                <Skeleton className="h-8" />
                <Skeleton className="h-8" />
              </div>
            ) : skills.length === 0 ? (
              <p className="p-6 text-center text-[13px] text-muted-foreground">
                No skills yet — drop a skill directory here (a folder with a SKILL.md in it), or
                sync the official Anthropic skills above. Every session mounts all skills at start.
              </p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead />
                    <TableHead>Name</TableHead>
                    <TableHead className="text-right">Files</TableHead>
                    <TableHead className="text-right">Size</TableHead>
                    <TableHead className="text-right">Updated</TableHead>
                    <TableHead />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {skills.map((skill) => (
                    <SkillRow
                      key={skill.name}
                      skill={skill}
                      now={now}
                      expanded={expanded === skill.name}
                      onToggle={() => setExpanded(expanded === skill.name ? null : skill.name)}
                      onDeleted={refresh}
                    />
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      </SkillDropZone>
    </div>
  );
}

function SkillRow({
  skill,
  now,
  expanded,
  onToggle,
  onDeleted,
}: {
  skill: SkillSummary;
  now: number;
  expanded: boolean;
  onToggle: () => void;
  onDeleted: () => void;
}) {
  const [flash, run] = useAction();
  const Chevron = expanded ? ChevronDown : ChevronRight;

  const remove = () => {
    if (!confirm(`Delete the whole skill ${skill.name}? Every file under it is removed.`)) return;
    run(async () => {
      await post(`/api/skills/${encodeURIComponent(skill.name)}/delete`);
      onDeleted();
      return `deleted ${skill.name}`;
    });
  };

  return (
    <>
      <TableRow onClick={onToggle} className="cursor-pointer">
        <TableCell className="w-8">
          <Chevron className="size-4 text-muted-foreground" />
        </TableCell>
        <TableCell className="font-mono text-[13px] font-medium">
          <Link
            href={`/skill?name=${encodeURIComponent(skill.name)}`}
            onClick={(e) => e.stopPropagation()}
            className="hover:underline"
          >
            {skill.name}
          </Link>
        </TableCell>
        <TableCell className="text-right font-mono text-xs">{skill.file_count}</TableCell>
        <TableCell className="text-right font-mono text-xs">{bytes(skill.total_size)}</TableCell>
        <TableCell className="text-right text-xs text-muted-foreground">
          {relTime(skill.updated, now)}
        </TableCell>
        <TableCell className="w-10 text-right">
          {flash ? (
            <span className="text-[11px] text-muted-foreground">{flash}</span>
          ) : (
            <Button
              variant="ghost"
              size="sm"
              title="Delete skill"
              onClick={(e) => {
                e.stopPropagation();
                remove();
              }}
            >
              <Trash2 />
            </Button>
          )}
        </TableCell>
      </TableRow>
      {expanded && <FilesRow name={skill.name} now={now} />}
    </>
  );
}

function FilesRow({ name, now }: { name: string; now: number }) {
  const { files } = useSkillFiles(name);

  return (
    <TableRow className="hover:bg-transparent">
      <TableCell />
      <TableCell colSpan={5} className="py-2">
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
