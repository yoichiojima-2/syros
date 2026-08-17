"use client";

import { useId, useRef, useState } from "react";
import { layoutDag, wouldCycle, type DagTask } from "@/lib/dag";
import { TASK_COLOR } from "@/components/run-badge";
import type { WorkflowTaskStatus } from "@/lib/types";
import { cn } from "@/lib/utils";

// The workflow DAG, drawn. Edges are one <svg> laid under absolutely positioned
// HTML nodes: the geometry wants SVG, but the nodes want the console's own type
// scale, truncation and focus rings, and a <button> gets keyboard access for
// free. Colors are palette tokens throughout (bg-ok, var(--border), …) so all
// six palettes and dark mode carry without a second definition.
//
// One component serves three callers — a workflow's definition, one run's live
// status, and the editor canvas — because they differ only in what a node says
// and whether dependencies can be dragged.

/** A node, already reduced to what the graph draws. `id` is whatever the caller
 *  keys dependencies by: stored task ids on the detail page, draft keys in the
 *  editor (where an id is still being typed and can't be a stable key). */
export type GraphTask = DagTask & {
  label: string;
  detail?: string | null;
  status?: WorkflowTaskStatus;
  title?: string;
  disabled?: boolean;
};

export function WorkflowGraph({
  tasks,
  selected,
  onSelect,
  onConnect,
  onDisconnect,
  empty = "No tasks yet.",
  className,
}: {
  tasks: GraphTask[];
  selected?: string | null;
  onSelect?: (id: string) => void;
  /** Enables the drag handles: `from` runs before `to`. */
  onConnect?: (from: string, to: string) => void;
  /** Enables clicking an edge to drop it. */
  onDisconnect?: (from: string, to: string) => void;
  empty?: string;
  className?: string;
}) {
  const canvas = useRef<HTMLDivElement>(null);
  const marker = useId().replace(/:/g, "");
  // A connection being dragged: where the pointer is, in canvas coordinates,
  // and which node it is over (null over empty space).
  const [drag, setDrag] = useState<{ from: string; x: number; y: number; over: string | null }>();

  const { nodes, edges, width, height } = layoutDag(tasks);
  if (tasks.length === 0) {
    return <p className={cn("py-10 text-center text-[13px] text-muted-foreground", className)}>{empty}</p>;
  }

  const point = (event: React.PointerEvent) => {
    const box = canvas.current?.getBoundingClientRect();
    return { x: event.clientX - (box?.left ?? 0), y: event.clientY - (box?.top ?? 0) };
  };
  const nodeAt = (x: number, y: number) =>
    nodes.find((n) => x >= n.x && x <= n.x + n.w && y >= n.y && y <= n.y + n.h)?.id ?? null;

  const illegal = drag?.over ? wouldCycle(tasks, drag.from, drag.over) : false;
  const dragging = drag !== undefined;

  return (
    // Wide graphs scroll sideways rather than squeezing the nodes; py-2 leaves
    // room for a selected node's focus ring.
    <div className={cn("overflow-x-auto py-2", className)}>
      <div
        ref={canvas}
        className="relative"
        style={{ width, height, minWidth: width }}
        onPointerMove={
          dragging
            ? (event) => {
                const { x, y } = point(event);
                setDrag((d) => (d ? { ...d, x, y, over: nodeAt(x, y) } : d));
              }
            : undefined
        }
        onPointerUp={
          dragging
            ? () => {
                if (drag.over && !wouldCycle(tasks, drag.from, drag.over)) {
                  onConnect?.(drag.from, drag.over);
                }
                setDrag(undefined);
              }
            : undefined
        }
        onPointerCancel={dragging ? () => setDrag(undefined) : undefined}
      >
        {/* Edges sit under the nodes and stay out of the way of their clicks;
            only the hit paths below opt back into pointer events. */}
        <svg
          className="pointer-events-none absolute inset-0"
          width={width}
          height={height}
          aria-hidden="true"
        >
          <defs>
            <marker
              id={marker}
              viewBox="0 0 8 8"
              refX="7"
              refY="4"
              markerWidth="5"
              markerHeight="5"
              orient="auto"
            >
              <path d="M 0 1 L 7 4 L 0 7 z" fill="var(--border)" />
            </marker>
          </defs>
          {edges.map((edge) => {
            const target = tasks.find((t) => t.id === edge.to);
            return (
              <g key={`${edge.from}->${edge.to}`}>
                <path
                  d={edge.d}
                  fill="none"
                  stroke="var(--border)"
                  strokeWidth={1.5}
                  markerEnd={`url(#${marker})`}
                  opacity={target?.status === "skipped" ? 0.4 : 1}
                />
                {onDisconnect && (
                  // A 14px transparent ribbon over the hairline: an edge has to
                  // be clickable without being thick.
                  <path
                    d={edge.d}
                    fill="none"
                    stroke="transparent"
                    strokeWidth={14}
                    className="pointer-events-auto cursor-pointer"
                    onClick={() => onDisconnect(edge.from, edge.to)}
                  >
                    <title>{`${edge.from} → ${edge.to} — click to remove`}</title>
                  </path>
                )}
              </g>
            );
          })}
          {drag && (
            <path
              d={`M ${nodeHandleX(nodes, drag.from)} ${nodeHandleY(nodes, drag.from)} L ${drag.x} ${drag.y}`}
              fill="none"
              strokeWidth={1.5}
              strokeDasharray="4 3"
              stroke={illegal ? "var(--destructive)" : "var(--primary)"}
            />
          )}
        </svg>

        {nodes.map(({ id, task, x, y, w, h }) => {
          const clickable = onSelect && !task.disabled;
          const isTarget = drag?.over === id && drag.from !== id;
          return (
            <div key={id}>
              <button
                type="button"
                disabled={!clickable}
                title={task.title}
                aria-current={selected === id ? "true" : undefined}
                onClick={clickable ? () => onSelect?.(id) : undefined}
                style={{ left: x, top: y, width: w, height: h }}
                className={cn(
                  "absolute flex flex-col justify-center gap-1 rounded-lg border border-border bg-card px-2.5 text-left transition-colors disabled:cursor-default",
                  clickable && "cursor-pointer hover:bg-secondary",
                  selected === id && "border-transparent ring-2 ring-ring",
                  task.status === "failed" && "border-destructive/50",
                  isTarget && (illegal ? "border-destructive" : "border-primary"),
                )}
              >
                <span className="flex items-center gap-1.5">
                  <span
                    className={cn(
                      "size-[7px] shrink-0 rounded-full",
                      task.status ? TASK_COLOR[task.status] : "bg-input",
                    )}
                  />
                  <span className="truncate font-mono text-[12px] font-semibold">{task.label}</span>
                </span>
                {task.detail && (
                  <span className="truncate text-[11px] text-muted-foreground">{task.detail}</span>
                )}
              </button>
              {onConnect && (
                // Pointer-only shortcut for what the "Runs after" chips already
                // do, so it stays out of the tab order and off the a11y tree.
                <span
                  aria-hidden="true"
                  onPointerDown={(event) => {
                    event.preventDefault();
                    // Capture on the canvas, not the handle: the move and up
                    // that finish the gesture have to land where the hit
                    // testing happens, including under touch's implicit capture.
                    canvas.current?.setPointerCapture(event.pointerId);
                    const at = point(event);
                    setDrag({ from: id, x: at.x, y: at.y, over: null });
                  }}
                  style={{ left: x + w - 5, top: y + h / 2 - 5 }}
                  // touch-none, or a touch drag is claimed as a pan of the
                  // scroll container and the gesture dies on pointercancel.
                  className="absolute size-2.5 cursor-crosshair touch-none rounded-full border border-input bg-card transition-colors hover:border-primary hover:bg-primary"
                  title={`Drag to a task that should run after ${task.label}`}
                />
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function nodeHandleX(nodes: { id: string; x: number; w: number }[], id: string) {
  const node = nodes.find((n) => n.id === id);
  return node ? node.x + node.w : 0;
}

function nodeHandleY(nodes: { id: string; y: number; h: number }[], id: string) {
  const node = nodes.find((n) => n.id === id);
  return node ? node.y + node.h / 2 : 0;
}
