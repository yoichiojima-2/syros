"use client";

import { useEffect, useRef } from "react";
import { Plus, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { ChoiceField, Field } from "@/components/option-fields";
import { wouldCycle } from "@/lib/dag";
import { cn } from "@/lib/utils";

/** One row of the editor. `dependsOn === null` means the user never touched the
 *  dependency control, so the key is left off the payload and the server applies
 *  its linear default (the previous task in the list) — that way inserting or
 *  removing a task re-chains the neighbours without the form re-deriving
 *  anything. Dependencies name the *editor keys* of other drafts, not their
 *  ids, so renaming a task (including through an empty box mid-edit) can't
 *  strand a dangling reference; ids are resolved at submit. `options` is
 *  carried opaquely: per-task options are settable from the CLI, and editing a
 *  workflow here must not strip them. */
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

/** The dependencies a task effectively has, as editor keys: an untouched draft
 *  chains to the task above it, which is what the server will do with the
 *  omitted key. Shared with the graph, which draws these as its edges. */
export function effectiveDeps(task: TaskDraft, index: number, tasks: TaskDraft[]): string[] {
  if (task.dependsOn !== null) return task.dependsOn;
  return index === 0 ? [] : [tasks[index - 1].key];
}

/** Databricks-Jobs-style task list: a card per task, "+ Add task" to grow the
 *  chain, and dependencies picked from the tasks above. A one-task list is the
 *  ordinary scheduled prompt.
 *
 *  Offering only the tasks above used to be enough to make a cycle
 *  unrepresentable; since the graph can draw an edge backwards, it isn't, so
 *  the chips carry the same `wouldCycle` guard the drag does.
 *
 *  The graph above it (see `workflow-graph-editor.tsx`) is the map; these cards
 *  stay the body being edited, so every task's prompt is readable at once and
 *  everything the drag shortcut does is also reachable from the keyboard. */
export function TaskListEditor({
  tasks,
  onChange,
  agents,
  selected,
  onSelect,
}: {
  tasks: TaskDraft[];
  onChange: (tasks: TaskDraft[]) => void;
  agents: string[];
  selected?: string | null;
  onSelect?: (key: string) => void;
}) {
  const patch = (index: number, fields: Partial<TaskDraft>) =>
    onChange(tasks.map((task, i) => (i === index ? { ...task, ...fields } : task)));

  // Removal drops the task from explicit dependency lists; untouched tasks
  // re-chain to their new neighbour on their own. Renames need no such
  // bookkeeping — dependencies are held by editor key.
  const remove = (index: number) => {
    const gone = tasks[index].key;
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

  // The same graph the canvas draws, keyed by draft key, so a chip and a drag
  // agree on which dependencies would close a loop.
  const graph = tasks.map((task, index) => ({
    id: task.key,
    depends_on: effectiveDeps(task, index, tasks),
  }));

  return (
    <div className="space-y-3">
      {tasks.map((task, index) => {
        const deps = effectiveDeps(task, index, tasks);
        // The tasks above, plus anything else this one already depends on —
        // a definition authored elsewhere may name a task listed later, and
        // an invisible dependency is worse than an out-of-order chip.
        const choices = [
          ...tasks.slice(0, index),
          ...tasks.slice(index + 1).filter((other) => deps.includes(other.key)),
        ];
        return (
          <TaskCard
            key={task.key}
            task={task}
            index={index}
            choices={choices}
            deps={deps}
            // Turning a dependency off never closes a loop; only adding one can.
            blocked={choices
              .filter((other) => !deps.includes(other.key) && wouldCycle(graph, other.key, task.key))
              .map((other) => other.key)}
            explicit={task.dependsOn !== null}
            agents={agents}
            selected={selected === task.key}
            onSelect={onSelect}
            onPatch={(fields) => patch(index, fields)}
            onRemove={tasks.length > 1 ? () => remove(index) : undefined}
          />
        );
      })}
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
  choices,
  deps,
  blocked,
  explicit,
  agents,
  selected,
  onSelect,
  onPatch,
  onRemove,
}: {
  task: TaskDraft;
  index: number;
  choices: TaskDraft[];
  deps: string[];
  blocked: string[];
  explicit: boolean;
  agents: string[];
  selected: boolean;
  onSelect?: (key: string) => void;
  onPatch: (fields: Partial<TaskDraft>) => void;
  onRemove?: () => void;
}) {
  // The first click materializes the default before toggling, so "the previous
  // task" stays selected unless the user actually turns it off.
  const toggleDep = (key: string) =>
    onPatch({
      dependsOn: deps.includes(key) ? deps.filter((d) => d !== key) : [...deps, key],
    });

  // Picking a node in the graph brings its card into view; "nearest" makes the
  // usual case — clicking the card itself — a no-op.
  const card = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (selected) card.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [selected]);

  return (
    <div
      ref={card}
      onFocusCapture={onSelect ? () => onSelect(task.key) : undefined}
      onClick={onSelect ? () => onSelect(task.key) : undefined}
      className={cn(
        "rounded-lg border border-border px-3 py-3",
        selected && "border-transparent ring-2 ring-ring",
      )}
    >
      <div className="mb-3 flex items-center gap-2">
        <span className="font-mono text-[11px] text-faint tabular-nums">{index + 1}</span>
        <Input
          value={task.id}
          onChange={(e) => onPatch({ id: e.target.value })}
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
          {/* A heading, not Field: Field is a <label>, and a label wrapping
              buttons forwards its own clicks into the first chip. */}
          <div className="space-y-1.5">
            <span className="flex items-baseline gap-1.5">
              <span className="text-[11px] font-medium tracking-[0.08em] text-muted-foreground uppercase">
                Runs after
              </span>
              <span className="text-[10px] text-faint">
                {index === 0 ? "first in the list" : "default: the task above"}
              </span>
            </span>
            {choices.length === 0 ? (
              <p className="pt-1 text-[12px] text-muted-foreground">
                runs first, when the workflow fires
              </p>
            ) : (
              <div className="flex flex-wrap items-center gap-1.5 pt-1">
                {choices.map((other) => {
                  const on = deps.includes(other.key);
                  const loops = blocked.includes(other.key);
                  return (
                    <button
                      key={other.key}
                      type="button"
                      aria-pressed={on}
                      disabled={loops}
                      title={
                        loops
                          ? `${other.id || "that task"} already runs after this one`
                          : undefined
                      }
                      onClick={() => toggleDep(other.key)}
                      className={cn(
                        "rounded-full border px-2.5 py-0.5 font-mono text-[11px] transition-colors",
                        on
                          ? "border-transparent bg-primary-soft text-foreground"
                          : "border-border text-muted-foreground hover:bg-secondary",
                        loops && "cursor-not-allowed text-faint opacity-50 hover:bg-transparent",
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
          </div>
        </div>
      </div>
    </div>
  );
}
