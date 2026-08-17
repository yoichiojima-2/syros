"use client";

import { useState } from "react";
import { effectiveDeps, TaskListEditor, type TaskDraft } from "@/components/task-list-editor";
import { WorkflowGraph, type GraphTask } from "@/components/workflow-graph";
import { compact } from "@/lib/format";

/** The workflow's task list, with its shape drawn above it.
 *
 *  The graph is the map and the cards below are the body: selecting a node
 *  brings its card into view, and dragging between nodes is a shortcut for the
 *  card's "Runs after" chips rather than a replacement — so a cycle stays
 *  unrepresentable, and nothing here is pointer-only.
 *
 *  Dependencies are addressed by draft key throughout (see `TaskDraft`), which
 *  is why the graph takes its ids from `key` and not from the task id the user
 *  is still typing. */
export function WorkflowGraphEditor({
  tasks,
  onChange,
  agents,
}: {
  tasks: TaskDraft[];
  onChange: (tasks: TaskDraft[]) => void;
  agents: string[];
}) {
  const [selected, setSelected] = useState<string | null>(null);

  const nodes: GraphTask[] = tasks.map((task, index) => ({
    id: task.key,
    label: task.id || "…",
    depends_on: effectiveDeps(task, index, tasks),
    detail: task.agent ? `as ${task.agent}` : firstLine(task.prompt) || "no prompt yet",
    title: task.prompt || undefined,
  }));

  /** Rewrite one draft's dependencies. Editing them always materializes the
   *  list — an untouched draft is chained by the server, so the first explicit
   *  change has to start from the same set the graph just drew. */
  const setDeps = (key: string, next: (deps: string[]) => string[]) =>
    onChange(
      tasks.map((task, index) =>
        task.key === key ? { ...task, dependsOn: next(effectiveDeps(task, index, tasks)) } : task,
      ),
    );

  return (
    <div className="space-y-3">
      <div className="rounded-lg border border-border bg-surface px-3 pt-1">
        <WorkflowGraph
          tasks={nodes}
          selected={selected}
          onSelect={setSelected}
          onConnect={(from, to) =>
            setDeps(to, (deps) => (deps.includes(from) ? deps : [...deps, from]))
          }
          onDisconnect={(from, to) => setDeps(to, (deps) => deps.filter((d) => d !== from))}
          empty="Add a task to start the graph."
        />
        <p className="pb-2 text-[10px] text-faint">
          Drag from a task&apos;s right edge onto another to run it after; click an edge to remove
          it. Tasks with no edge between them run at the same time.
        </p>
      </div>
      <TaskListEditor
        tasks={tasks}
        onChange={onChange}
        agents={agents}
        selected={selected}
        onSelect={setSelected}
      />
    </div>
  );
}

function firstLine(prompt: string): string {
  return compact(prompt.trim().split("\n")[0] ?? "", 26);
}
