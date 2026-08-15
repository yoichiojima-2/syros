"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { ChevronDown, ChevronRight, DownloadCloud, Search, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { useAction, useNow, useSkillFiles, useSkills } from "@/lib/hooks";
import { post } from "@/lib/api";
import { bytes, relTime } from "@/lib/format";
import type { SkillSummary } from "@/lib/types";
import type { SyncSkillsResponse } from "@/lib/types";

// Skills live under skills/{name}/ (global) and team-skills/{workspace}/{name}/
// in the bucket, and are mounted into a session's HOME at run start, so what
// this page shows is exactly what the next run will discover. "Sync official
// skills" seeds the global prefix with editable copies of
// github.com/anthropics/skills.
//
// The row leads with the SKILL.md description rather than the byte counts: it
// is the text the model matches on when deciding whether a skill fires, so it
// is the only thing that distinguishes twenty rows of slugs from each other.

export default function SkillsPage() {
  const skills = useSkills();
  const now = useNow();
  const [expanded, setExpanded] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [flash, run] = useAction();

  const sync = () =>
    run(async () => {
      const result = await post<SyncSkillsResponse>("/api/skills/sync");
      const skipped = result.skipped.length ? `, ${result.skipped.length} skipped (too large)` : "";
      return `synced ${result.files} file(s) across ${result.skills.length} skill(s)${skipped}`;
    });

  const term = query.trim().toLowerCase();
  const hits = useMemo(
    () =>
      (skills ?? []).filter(
        (skill) =>
          !term ||
          skill.name.toLowerCase().includes(term) ||
          (skill.description ?? "").toLowerCase().includes(term),
      ),
    [skills, term],
  );

  const global = hits.filter((skill) => !skill.workspace);
  const scoped = hits.filter((skill) => skill.workspace);

  return (
    <div className="min-h-0 flex-1 space-y-5 overflow-y-auto p-4 sm:p-6">
      <div className="flex items-center gap-3">
        <h1 className="flex-1 font-serif text-2xl tracking-tight">Skills</h1>
        {flash && <span className="text-[11px] text-muted-foreground">{flash}</span>}
        <Button variant="outline" size="sm" onClick={sync}>
          <DownloadCloud /> Sync official skills
        </Button>
      </div>
      <Card>
        <CardContent className="px-0 py-0">
          {skills === null ? (
            <div className="space-y-2 p-4">
              <Skeleton className="h-8" />
              <Skeleton className="h-8" />
            </div>
          ) : skills.length === 0 ? (
            <p className="p-6 text-center text-[13px] text-muted-foreground">
              No skills yet — sync the official Anthropic skills above, or upload your own from a
              skill&apos;s page. Every session mounts all skills at start.
            </p>
          ) : (
            <>
              <div className="flex flex-wrap items-center gap-3 border-b px-3 py-2.5">
                <div className="relative flex min-w-[240px] flex-1 items-center">
                  <Search className="pointer-events-none absolute left-2.5 size-3.5 text-faint" />
                  <Input
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    placeholder="Filter by name or what it does"
                    aria-label="Filter skills"
                    className="h-8 pl-8 text-[13px]"
                  />
                </div>
                <span className="font-mono text-[11px] whitespace-nowrap text-faint">
                  {term
                    ? `${hits.length} of ${skills.length}`
                    : `${skills.length} skills · mounted at session start`}
                </span>
              </div>
              {hits.length === 0 ? (
                <p className="px-3 py-9 text-center text-[13px] text-muted-foreground">
                  Nothing matches “{query.trim()}”.
                </p>
              ) : (
                <>
                  <Section title="Global" />
                  {global.map((skill) => (
                    <SkillRow
                      key={skill.name}
                      skill={skill}
                      now={now}
                      query={term}
                      expanded={expanded === skill.name}
                      onToggle={() => setExpanded(expanded === skill.name ? null : skill.name)}
                    />
                  ))}
                  {(scoped.length > 0 || !term) && <Section title="Workspace" />}
                  {scoped.map((skill) => {
                    const key = `${skill.workspace}/${skill.name}`;
                    return (
                      <SkillRow
                        key={key}
                        skill={skill}
                        now={now}
                        query={term}
                        expanded={expanded === key}
                        onToggle={() => setExpanded(expanded === key ? null : key)}
                      />
                    );
                  })}
                  {scoped.length === 0 && !term && (
                    <p className="px-3.5 pt-0.5 pb-3 text-[12.5px] text-muted-foreground">
                      None yet — a workspace skill lives under{" "}
                      <code className="font-mono text-[11.5px] text-faint">
                        team-skills/&#123;workspace&#125;/
                      </code>{" "}
                      and mounts only for that workspace, shadowing a global of the same name.
                    </p>
                  )}
                </>
              )}
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function Section({ title }: { title: string }) {
  return (
    <div className="flex items-center gap-2.5 px-3.5 pt-3 pb-1.5">
      <span className="text-[11px] font-semibold tracking-[0.08em] text-faint uppercase">
        {title}
      </span>
      <span className="h-px flex-1 bg-border" />
    </div>
  );
}

/** Highlight the filter match in place, so it is obvious why a row survived. */
function Highlight({ text, query }: { text: string; query: string }) {
  const at = query ? text.toLowerCase().indexOf(query) : -1;
  if (at < 0) return <>{text}</>;
  return (
    <>
      {text.slice(0, at)}
      <mark className="rounded-[2px] bg-primary-soft text-foreground">
        {text.slice(at, at + query.length)}
      </mark>
      {text.slice(at + query.length)}
    </>
  );
}

function SkillRow({
  skill,
  now,
  query,
  expanded,
  onToggle,
}: {
  skill: SkillSummary;
  now: number;
  query: string;
  expanded: boolean;
  onToggle: () => void;
}) {
  const [flash, run] = useAction();
  const Chevron = expanded ? ChevronDown : ChevronRight;
  const href = `/skill?name=${encodeURIComponent(skill.name)}${
    skill.workspace ? `&workspace=${encodeURIComponent(skill.workspace)}` : ""
  }`;

  const remove = () => {
    if (!confirm(`Delete the whole skill ${skill.name}? Every file under it is removed.`)) return;
    run(async () => {
      await post(`/api/skills/${encodeURIComponent(skill.name)}/delete`, {
        workspace: skill.workspace,
      });
      return `deleted ${skill.name}`;
    });
  };

  return (
    <>
      <div
        onClick={onToggle}
        className="group grid cursor-pointer grid-cols-[1.25rem_minmax(0,1fr)_auto_auto] items-baseline gap-x-3 px-3.5 py-2 hover:bg-muted"
      >
        <Chevron className="size-3.5 self-center text-faint" />
        <Link
          href={href}
          onClick={(e) => e.stopPropagation()}
          className="min-w-0 truncate font-mono text-[13px] font-medium hover:underline"
        >
          <Highlight text={skill.name} query={query} />
        </Link>
        <span className="self-center font-mono text-[11px] tabular-nums whitespace-nowrap text-faint">
          {skill.file_count} file{skill.file_count === 1 ? "" : "s"} · {bytes(skill.total_size)} ·{" "}
          {relTime(skill.updated, now)}
        </span>
        <span className="w-8 self-center text-right">
          {flash ? (
            <span className="text-[11px] text-muted-foreground">{flash}</span>
          ) : (
            <Button
              variant="ghost"
              size="sm"
              title="Delete skill"
              className="opacity-0 group-hover:opacity-100 focus-visible:opacity-100"
              onClick={(e) => {
                e.stopPropagation();
                remove();
              }}
            >
              <Trash2 />
            </Button>
          )}
        </span>
        <p className="col-start-2 mt-0.5 line-clamp-1 text-[12.5px] text-muted-foreground">
          {skill.description ? (
            <Highlight text={skill.description} query={query} />
          ) : (
            <span className="text-faint italic">no description in SKILL.md</span>
          )}
        </p>
      </div>
      {expanded && <FilesRow name={skill.name} workspace={skill.workspace} now={now} />}
    </>
  );
}

function FilesRow({
  name,
  workspace,
  now,
}: {
  name: string;
  workspace: string | null;
  now: number;
}) {
  const { files } = useSkillFiles(name, workspace);

  return (
    <div className="bg-muted px-3.5 py-2 pl-11">
      {files === null ? (
        <Skeleton className="h-5 w-48" />
      ) : files.length === 0 ? (
        <span className="text-[12px] text-muted-foreground">empty</span>
      ) : (
        <ul className="space-y-0.5">
          {files.map((file) => (
            <li key={file.name} className="flex items-baseline gap-3 font-mono text-[12px]">
              <span className="min-w-0 truncate">{file.name}</span>
              <span className="text-muted-foreground">{bytes(file.size)}</span>
              <span className="text-faint">{relTime(file.updated, now)}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
