// Layered layout for the workflow DAG — the geometry behind <WorkflowGraph>.
//
// Pure and dependency-free: workflow graphs are a handful of nodes, so the
// classic longest-path layering plus one barycenter pass is both enough to keep
// edges from crossing and small enough to read. Nothing here knows about React,
// and nothing stores coordinates — a graph is laid out from its dependencies
// every render, so a workflow's definition never grows canvas state.

/** The shape both a stored task (`depends_on` by id) and an editor draft
 *  (dependencies by draft key) are mapped onto before layout. */
export type DagTask = { id: string; depends_on: string[] };

export const NODE_W = 176;
export const NODE_H = 62;
const GAP_X = 48;
const GAP_Y = 14;

export type DagNode<T> = {
  id: string;
  task: T;
  layer: number;
  row: number;
  x: number;
  y: number;
  w: number;
  h: number;
};

export type DagEdge = {
  from: string;
  to: string;
  d: string; // SVG path
};

export type DagLayout<T> = {
  nodes: DagNode<T>[];
  edges: DagEdge[];
  width: number;
  height: number;
};

export type DagOptions = { nodeWidth?: number; nodeHeight?: number };

/** Longest-path layer per node: a root sits in layer 0, everything else one
 *  past its deepest dependency.
 *
 *  Memoized depth-first, and a back edge contributes 0 rather than recursing:
 *  the server rejects cycles, but a half-finished draft in the editor is laid
 *  out by the same code and must not hang. Unknown dependency ids (a task the
 *  user is mid-rename) are ignored for the same reason. */
function layerOf(tasks: DagTask[]): Map<string, number> {
  const byId = new Map(tasks.map((task) => [task.id, task]));
  const layers = new Map<string, number>();
  const visiting = new Set<string>();

  const walk = (id: string): number => {
    const done = layers.get(id);
    if (done !== undefined) return done;
    if (visiting.has(id)) return 0;
    visiting.add(id);
    let layer = 0;
    for (const dep of byId.get(id)?.depends_on ?? []) {
      if (byId.has(dep)) layer = Math.max(layer, walk(dep) + 1);
    }
    visiting.delete(id);
    layers.set(id, layer);
    return layer;
  };

  for (const task of tasks) walk(task.id);
  return layers;
}

/** Positions for every node, plus the bezier path of every edge.
 *
 *  Left to right, the way a pipe reads: layer 0 on the left, each layer a
 *  column, columns centred against the tallest one. Rows within a column start
 *  in definition order and then take one barycenter pass — each node slides to
 *  the mean row of its dependencies — which is enough to keep a diamond's two
 *  branches from crossing without a full Sugiyama pass. */
export function layoutDag<T extends DagTask>(tasks: T[], opts: DagOptions = {}): DagLayout<T> {
  const w = opts.nodeWidth ?? NODE_W;
  const h = opts.nodeHeight ?? NODE_H;
  if (tasks.length === 0) return { nodes: [], edges: [], width: 0, height: 0 };

  const layers = layerOf(tasks);
  const columns: T[][] = [];
  for (const task of tasks) {
    const layer = layers.get(task.id) ?? 0;
    (columns[layer] ??= []).push(task);
  }
  for (let i = 0; i < columns.length; i++) columns[i] ??= [];

  // Row assignment, column by column: by the time a column is ordered its
  // dependencies already have rows, so the barycenter is well defined. A node
  // whose deps all sit outside the previous columns keeps its own position.
  const rows = new Map<string, number>();
  for (const column of columns) {
    const bary = new Map<string, number>();
    column.forEach((task, index) => {
      const known = task.depends_on.map((dep) => rows.get(dep)).filter((r) => r !== undefined);
      bary.set(task.id, known.length ? known.reduce((a, b) => a + b, 0) / known.length : index);
    });
    column
      .map((task, index) => ({ task, index }))
      .sort((a, b) => bary.get(a.task.id)! - bary.get(b.task.id)! || a.index - b.index)
      .forEach(({ task }, row) => rows.set(task.id, row));
  }

  const tallest = Math.max(...columns.map((column) => column.length));
  const height = tallest * h + (tallest - 1) * GAP_Y;
  const width = columns.length * w + (columns.length - 1) * GAP_X;

  const nodes: DagNode<T>[] = [];
  columns.forEach((column, layer) => {
    const columnHeight = column.length * h + (column.length - 1) * GAP_Y;
    const top = (height - columnHeight) / 2;
    for (const task of column) {
      const row = rows.get(task.id) ?? 0;
      nodes.push({
        id: task.id,
        task,
        layer,
        row,
        x: layer * (w + GAP_X),
        y: top + row * (h + GAP_Y),
        w,
        h,
      });
    }
  });

  const at = new Map(nodes.map((node) => [node.id, node]));
  const edges: DagEdge[] = [];
  for (const task of tasks) {
    for (const dep of task.depends_on) {
      const from = at.get(dep);
      const to = at.get(task.id);
      if (!from || !to) continue;
      edges.push({ from: dep, to: task.id, d: edgePath(from, to) });
    }
  }

  return { nodes, edges, width, height };
}

/** Right edge of the source to the left edge of the target. The control points
 *  leave and arrive horizontally, so an edge always meets a node square-on. */
export function edgePath(
  from: { x: number; y: number; w: number; h: number },
  to: { x: number; y: number; h: number },
): string {
  const x1 = from.x + from.w;
  const y1 = from.y + from.h / 2;
  const x2 = to.x;
  const y2 = to.y + to.h / 2;
  const bend = Math.max(20, (x2 - x1) / 2);
  return `M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}`;
}

/** Would making `to` run after `from` close a loop? True when `from` already
 *  depends on `to`, transitively — the guard the editor's drag needs so a
 *  pointer can't author what the server would reject. */
export function wouldCycle(tasks: DagTask[], from: string, to: string): boolean {
  if (from === to) return true;
  const byId = new Map(tasks.map((task) => [task.id, task]));
  const seen = new Set<string>();
  const stack = [from];
  while (stack.length) {
    const id = stack.pop()!;
    if (id === to) return true;
    if (seen.has(id)) continue;
    seen.add(id);
    for (const dep of byId.get(id)?.depends_on ?? []) stack.push(dep);
  }
  return false;
}
