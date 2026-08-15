"use client";

import { Plus, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { ChoiceField, Field } from "@/components/option-fields";
import { cn } from "@/lib/utils";

/** One row of the editor. `dependsOn === null` means the user never touched the
 *  dependency control, so the key is left off the payload and the server applies
 *  its linear default (the previous task in the list) — that way inserting or
 *  removing a task re-chains the neighbours without the form re-deriving
 *  anything. `options` is carried opaquely: per-task options are settable from
 *  the CLI, and editing a workflow here must not strip them. */
export type TaskDraft = {
  key: string;
  id: string;
  prompt: string;
  agent: string;
  dependsOn: string[] | null;
  options?: Record<string, unknown>;
};

let counter = 0;
const uid = () => `t${++counter}`;

export function emptyTask(id: string): TaskDraft {
  return { key: uid(), id, prompt: "", agent: "", dependsOn: null };
}

/** `task-2`, `task-3`, … skipping ids already taken. */
function nextId(tasks: TaskDraft[]): string {
  const taken = new Set(tasks.map((t) => t.id));
  for (let n = tasks.length + 1; ; n++) {
    const id = `task-${n}`;
    if (!taken.has(id)) return id;
  }
}

/** The dependencies a task effectively has: an untouched draft chains to the
 *  task above it, which is what the server will do with the omitted key. */
function effectiveDeps(task: TaskDraft, index: number, tasks: TaskDraft[]): string[] {
  if (task.dependsOn !== null) return task.dependsOn;
  return index === 0 ? [] : [tasks[index - 1].id];
}

/** Databricks-Jobs-style task list: a card per task, "+ Add task" to grow the
 *  chain, and dependencies picked from the tasks above (which makes a cycle
 *  unrepresentable). A one-task list is the ordinary scheduled prompt. */
export function TaskListEditor({
  tasks,
  onChange,
  agents,
}: {
  tasks: TaskDraft[];
  onChange: (tasks: TaskDraft[]) => void;
  agents: string[];
}) {
  const patch = (index: number, fields: Partial<TaskDraft>) =>
    onChange(tasks.map((task, i) => (i === index ? { ...task, ...fields } : task)));

  // A rename follows through into everyone who named this task explicitly.
  const rename = (index: number, id: string) => {
    const previous = tasks[index].id;
    onChange(
      tasks.map((task, i) => {
        if (i === index) return { ...task, id };
        if (!previous || task.dependsOn === null) return task;
        return { ...task, dependsOn: task.dependsOn.map((d) => (d === previous ? id : d)) };
      }),
    );
  };

  // Removal drops the id from explicit dependency lists; untouched tasks
  // re-chain to their new neighbour on their own.
  const remove = (index: number) => {
    const gone = tasks[index].id;
    onChange(
      tasks
        .filter((_, i) => i !== index)
        .map((task) =>
          task.dependsOn === null
            ? task
            : { ...task, dependsOn: task.dependsOn.filter((d) => d !== gone) },
        ),
    );
  };

  const duplicates = tasks
    .map((task) => task.id)
    .filter((id, i, ids) => id && ids.indexOf(id) !== i);

  return (
    <div className="space-y-3">
      {tasks.map((task, index) => (
        <TaskCard
          key={task.key}
          task={task}
          index={index}
          earlier={tasks.slice(0, index)}
          deps={effectiveDeps(task, index, tasks)}
          explicit={task.dependsOn !== null}
          agents={agents}
          onPatch={(fields) => patch(index, fields)}
          onRename={(id) => rename(index, id)}
          onRemove={tasks.length > 1 ? () => remove(index) : undefined}
        />
      ))}
      <div className="flex items-center gap-3">
        <Button type="button" variant="outline" size="sm" onClick={() => onChange([...tasks, emptyTask(nextId(tasks))])}>
          <Plus />
          Add task
        </Button>
        <span className="text-[11px] text-muted-foreground">
          Each task runs as a fresh session; a new task runs after the one above it.
        </span>
      </div>
      {duplicates.length > 0 && (
        <p className="text-[12px] text-destructive">duplicate task id: {duplicates[0]}</p>
      )}
    </div>
  );
}

function TaskCard({
  task,
  index,
  earlier,
  deps,
  explicit,
  agents,
  onPatch,
  onRename,
  onRemove,
}: {
  task: TaskDraft;
  index: number;
  earlier: TaskDraft[];
  deps: string[];
  explicit: boolean;
  agents: string[];
  onPatch: (fields: Partial<TaskDraft>) => void;
  onRename: (id: string) => void;
  onRemove?: () => void;
}) {
  // The first click materializes the default before toggling, so "the previous
  // task" stays selected unless the user actually turns it off.
  const toggleDep = (id: string) =>
    onPatch({
      dependsOn: deps.includes(id) ? deps.filter((d) => d !== id) : [...deps, id],
    });

  return (
    <div className="rounded-lg border border-border px-3 py-3">
      <div className="mb-3 flex items-center gap-2">
        <span className="font-mono text-[11px] text-faint tabular-nums">{index + 1}</span>
        <Input
          value={task.id}
          onChange={(e) => onRename(e.target.value)}
          placeholder="task id"
          required
          aria-label="Task id"
          className="h-7 max-w-[14rem] font-mono text-[12px]"
        />
        <span className="flex-1" />
        {onRemove && (
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="size-7"
            title="Remove task"
            onClick={onRemove}
          >
            <X />
          </Button>
        )}
      </div>
      <div className="space-y-3">
        <Field
          label="Prompt"
          hint={index > 0 ? "{{tasks.<id>.result}} pastes an upstream task's output" : undefined}
        >
          <Textarea
            value={task.prompt}
            onChange={(e) => onPatch({ prompt: e.target.value })}
            rows={3}
            required
            placeholder="Profile the CSVs in the workspace and write report.md"
            className="rounded-lg border border-input bg-card px-3 py-2 text-[13px]"
          />
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Agent" hint="stored persona">
            <ChoiceField
              value={task.agent}
              onChange={(agent) => onPatch({ agent })}
              choices={agents}
              noneLabel="none"
            />
          </Field>
          <Field
            label="Runs after"
            hint={index === 0 ? "first task" : "default: the task above"}
          >
            {index === 0 ? (
              <p className="pt-1 text-[12px] text-muted-foreground">
                runs first, when the workflow fires
              </p>
            ) : (
              <div className="flex flex-wrap items-center gap-1.5 pt-1">
                {earlier.map((other) => {
                  const on = deps.includes(other.id);
                  return (
                    <button
                      key={other.key}
                      type="button"
                      aria-pressed={on}
                      onClick={() => toggleDep(other.id)}
                      className={cn(
                        "rounded-full border px-2.5 py-0.5 font-mono text-[11px] transition-colors",
                        on
                          ? "border-transparent bg-primary-soft text-foreground"
                          : "border-border text-muted-foreground hover:bg-secondary",
                      )}
                    >
                      {other.id || "…"}
                    </button>
                  );
                })}
                {explicit && deps.length === 0 && (
                  <span className="text-[11px] text-faint">starts with the run</span>
                )}
              </div>
            )}
          </Field>
        </div>
      </div>
    </div>
  );
}
