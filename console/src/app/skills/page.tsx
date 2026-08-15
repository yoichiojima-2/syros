"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  ChevronDown,
  ChevronRight,
  DownloadCloud,
  FilePlus2,
  Globe,
  Trash2,
} from "lucide-react";
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
import { useAction, useNow, useSkillFiles, useSkills } from "@/lib/hooks";
import { post } from "@/lib/api";
import { bytes, relTime } from "@/lib/format";
import type { SkillSummary, SyncSkillsResponse } from "@/lib/types";
import { cn } from "@/lib/utils";

// The skill catalog: skills/{name}/ in the bucket, the one place skill content
// lives. Nothing here is mounted by itself — a run mounts the skills its
// options install, so each row shows where it is installed ("global" = the
// settings default, plus any workspace that installs it). Installing happens
// on the target: the Skills field on global settings, a workspace, an agent or
// a single session. "Sync official skills" seeds the catalog with editable
// copies of github.com/anthropics/skills.

export default function SkillsPage() {
  const router = useRouter();
  const skills = useSkills();
  const now = useNow();
  const [expanded, setExpanded] = useState<string | null>(null);
  const [flash, run] = useAction();

  // A skill is a directory, so "new" is just the first file: the editor page
  // creates the prefix when SKILL.md is saved.
  const create = () => {
    const name = prompt("New skill name (lowercase, [a-z0-9_-])");
    if (name) router.push(`/skill?name=${encodeURIComponent(name)}`);
  };

  const sync = () =>
    run(async () => {
      const result = await post<SyncSkillsResponse>("/api/skills/sync");
      const skipped = result.skipped.length ? `, ${result.skipped.length} skipped (too large)` : "";
      return `synced ${result.files} file(s) across ${result.skills.length} skill(s)${skipped}`;
    });

  return (
    <div className="min-h-0 flex-1 space-y-5 overflow-y-auto p-4 sm:p-6">
      <div className="flex items-center gap-3">
        <h1 className="flex-1 font-serif text-2xl tracking-tight">Skills</h1>
        {flash && <span className="text-[11px] text-muted-foreground">{flash}</span>}
        <Button variant="outline" size="sm" onClick={create}>
          <FilePlus2 /> New skill
        </Button>
        <Button variant="outline" size="sm" onClick={sync}>
          <DownloadCloud /> Sync official skills
        </Button>
      </div>
      <Card>
        <CardContent className="px-2 py-2">
          {skills === null ? (
            <div className="space-y-2 p-2">
              <Skeleton className="h-8" />
              <Skeleton className="h-8" />
            </div>
          ) : skills.length === 0 ? (
            <p className="p-6 text-center text-[13px] text-muted-foreground">
              No skills yet — sync the official Anthropic skills above, or write your own. A skill
              runs where it is installed: pick it in the Skills field on a workspace, an agent, or
              global settings.
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead />
                  <TableHead>Name</TableHead>
                  <TableHead>Installed in</TableHead>
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

function SkillRow({
  skill,
  now,
  expanded,
  onToggle,
}: {
  skill: SkillSummary;
  now: number;
  expanded: boolean;
  onToggle: () => void;
}) {
  const [flash, run] = useAction();
  const Chevron = expanded ? ChevronDown : ChevronRight;
  const global = skill.installed_in.includes("global");

  // The one install worth a shortcut: everywhere, i.e. the global settings
  // default. Per-workspace and per-agent installs are a field on those forms,
  // where they sit next to the rest of that target's options.
  const toggleGlobal = () =>
    run(async () => {
      await post(`/api/skills/${encodeURIComponent(skill.name)}/install`, { installed: !global });
      return global ? `uninstalled ${skill.name} globally` : `installed ${skill.name} everywhere`;
    });

  const remove = () => {
    if (!confirm(`Delete the whole skill ${skill.name}? Every file under it is removed.`)) return;
    run(async () => {
      await post(`/api/skills/${encodeURIComponent(skill.name)}/delete`);
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
        <TableCell className="py-1.5">
          {skill.installed_in.length === 0 ? (
            <span className="text-[11px] text-faint">not installed</span>
          ) : (
            <span className="flex flex-wrap gap-1">
              {skill.installed_in.map((where) => (
                <span
                  key={where}
                  className="rounded-full bg-secondary px-2 py-0.5 font-mono text-[10px] text-muted-foreground"
                >
                  {where}
                </span>
              ))}
            </span>
          )}
        </TableCell>
        <TableCell className="text-right font-mono text-xs">{skill.file_count}</TableCell>
        <TableCell className="text-right font-mono text-xs">{bytes(skill.total_size)}</TableCell>
        <TableCell className="text-right text-xs text-muted-foreground">
          {relTime(skill.updated, now)}
        </TableCell>
        <TableCell className="w-20 text-right">
          {flash ? (
            <span className="text-[11px] text-muted-foreground">{flash}</span>
          ) : (
            <>
              <Button
                variant="ghost"
                size="sm"
                aria-pressed={global}
                title={global ? "Remove from the global default" : "Install everywhere"}
                className={global ? "text-foreground" : "text-faint"}
                onClick={(e) => {
                  e.stopPropagation();
                  toggleGlobal();
                }}
              >
                <Globe />
              </Button>
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
            </>
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
