# Design: workflows

Status: draft for discussion. Nothing here is implemented.

## The itch

We want `claude -p "task 1" | claude -p "task 2"` as a platform feature: a
named chain of one-shot agent tasks, Databricks-Jobs style. But adding a
third orchestration noun next to *agents* and *deployments* makes the
concept model wobble — is an agent the persona or the task? Is a workflow a
list of deployments? This doc settles the concept model first, then the
mechanics. The codebase is experimental, so renames and breaking changes
are on the table; the goal is the model we would pick starting fresh.

## What we have today

| concept | where | what it is |
|---|---|---|
| `AgentOptions` | `options.py` | serializable runtime settings (model, tools, workspace, budgets) |
| agent | `agents/{name}` | stored options + description — *the persona a session runs as* |
| deployment | `deployments/{name}` | cron + **one inline unit of work** (prompt, agent ref, option overrides) |
| session | `sessions/{id}` | the run: journal, audit trail, cost, title/summary |
| workspace | `workspaces/{name}` | shared directory + option defaults + exclusive lease |
| artifact space | GCS prefix | shared files, no lease, per-file last-writer-wins |

The load-bearing observation: **a deployment is already a schedule stapled to
exactly one anonymous task.** The `(prompt, agent, options)` triple inside it
is the unit of work; it just has no name and no plural. A workflow feature is
not a new kind of thing — it is letting a definition hold *N* of these triples
instead of one.

## What the industry does

Every mature orchestrator separates the same four things and names each one
(full survey table at the end):

1. **the persona / config** — who or on-what the work runs
2. **the unit of work** — one prompt / notebook / step
3. **the definition** — the named DAG of units, which **owns the schedule**
4. **the run** — one execution of the definition (always a distinct noun
   from the definition: Job→Run, DAG→DagRun, Workflow→Execution)

Three conventions matter for us:

- **Agent platforms use "agent" for the persona.** CrewAI is the cleanest
  precedent — it needed both nouns for exactly our distinction: `Agent` =
  role/goal/backstory (reusable across tasks), `Task` = description +
  expected output + assigned agent, `Crew` = the assembly. OpenAI's Agents
  SDK (`Agent` = instructions + model + tools; work arrives as run input) and
  Claude Agent SDK subagents agree. Renaming our agents to "personas" and
  reusing "agent" for the one-shot task would swim against every SDK our
  users already know.
- **The schedule attaches to the definition, and the single-task definition
  is the degenerate case, not a separate feature.** Databricks has no
  standalone "scheduled notebook": a Job holds tasks (one or many), the
  schedule/trigger/`max_concurrent_runs` hang off the Job, and a Run fans out
  task runs. Airflow (DAG owns `schedule`), GitHub Actions (workflow owns
  `on:`), Temporal (schedules target workflows) all agree. The
  counter-example proves the rule: Google Workflows keeps the scheduler
  external (Cloud Scheduler calls the execution API), which means two
  consoles and two IAM setups, and users dislike it.
- **Data passes as small, named, producer-namespaced values — not ambient
  state.** Databricks task values (`{{tasks.<name>.values.<key>}}`, JSON,
  48 KiB cap), GitHub Actions `needs.<job>.outputs.<k>`, Airflow XCom
  (scoped per run). The cautionary tale is Step Functions'
  InputPath/Parameters/ResultSelector/ResultPath/OutputPath pipeline — the
  most complained-about part of that product. For chained LLM tasks the 90%
  case is simpler than all of these: the previous step's final text is the
  next prompt's input.

One more precedent worth naming: **Prefect's schedule-carrying object is
literally called a Deployment** (flow ref + schedule + default parameters).
Our current name wasn't wrong so much as our object was one task short.

## Proposed model

Three stored nouns, one of them renamed away:

| concept | noun | stored where |
|---|---|---|
| runtime settings | `AgentOptions` | unchanged |
| persona | **agent** | `agents/{name}` — unchanged |
| unit of work | **task** | an element *inside* a workflow, not a collection |
| definition + schedule | **workflow** | `workflows/{name}` |
| run of a task | **session** | `sessions/{id}` — unchanged, carries provenance |
| run of a workflow | **run** | `workflows/{name}/runs/{run_id}` |

`deployments/{name}` is deleted. A deployment becomes a workflow with one
task; the CLI keeps the ergonomics (`syros workflows create --prompt ...`
builds the one-task workflow without asking the user to think about tasks).

So the user-facing sentence is:

> An **agent** is who; a **task** is one prompt run as some agent; a
> **workflow** is a named list of tasks with an optional schedule; every task
> run is an ordinary **session**.

This also lines up with Anthropic's published taxonomy ("Building Effective
Agents"): a *workflow* is LLM calls orchestrated through predefined paths —
our workflows — while an *agent* directs its own process — our interactive
sessions. Our docs can lean on that distinction verbatim.

### Why not persona / agent / workflow (the rename we considered)

`persona → runtime settings, agent → one-shot task` reads nicely in
isolation, but "agent = a single fire-and-forget prompt" collides with what
agent means in claude_agent_sdk, CrewAI, OpenAI's SDK, and our own README.
The industry already picked: agent is the durable identity, task is the unit
of work. Keeping *agent = persona* also means `agents/{name}`,
`AgentOptions.agent`, and the resolution chain in `agents.resolve` are
untouched.

The name that *was* wrong is **deployment** — it never deployed anything; it
was a cron trigger with one task inside. It goes away entirely rather than
getting a sibling.

Other names considered:

- **"job"** — rejected: `AgentOptions.job` already means the Cloud Run Job.
- **"step"** vs "task" for the unit: Temporal deliberately avoided "task"
  (reserved for internal queue items), GitHub/Google Workflows say step.
  syros has no competing internal "task", the feature is pitched as
  Databricks-style, and CrewAI/Databricks/Airflow all say task — so: task.
- **"pipeline"** — fine, but "workflow" matches the agent ecosystem. The
  `workspaces`/`workflows` adjacency in the CLI is unfortunate but
  survivable — completion diverges at the fifth character.

## Schema

```
workflows/{name}
    tasks: [                        ordered; a one-task list is the common case
      { id: "research",             name within the workflow (validate_name)
        prompt: "...",              may reference {{tasks.<id>.result}}
        agent: "analyst" | null,    persona ref, resolved fresh at fire time
        options: {...},             per-task AgentOptions overrides (serialized subset)
        depends_on: ["..."] | null  null = the previous task (linear default);
      },                            [] = a root task (starts immediately)
      ...
    ]
    options: {...}                  shared defaults for every task (the layer a
                                    5-task workflow states "workspace: x" in once)
    schedule: { cron, timezone } | null    null = manual-only (run_now)
    enabled: bool                   false pauses the schedule; run_now still works
    next_run_at                     epoch seconds; the tick's only cursor (as today)
    last_run_at, last_run_id, last_error
    run_count, skip_count           display counters ("runs" would collide with
                                    the runs/ subcollection)
    created_by

workflows/{name}/runs/{run_id}
    trigger: "schedule" | "manual"
    status: "running" | "succeeded" | "failed"
    started_at, finished_at
    spec: [...]                     the workflow's tasks array captured at launch —
                                    advancement never re-reads the (editable) workflow
    tasks: {                        per-task state, the advancement transaction target
      research: { status: "pending"|"running"|"succeeded"|"failed"|"skipped",
                  session_id, result, started_at, finished_at },
      ...
    }

sessions/{id}                       gains provenance fields (replacing `deployment`)
    workflow: name | null
    run_id: run id | null
    task: task id | null
    trigger: "schedule" | "manual" | "api"
```

Notes:

- **One merge rule, layers ordered by proximity to the run.** At fire time a
  task's options resolve through the existing per-field merge
  (`agents.merge`), most-specific layer winning:

  ```
  task.options ← workflow.options ← agent ← workspace ← settings/global ← "sonnet"
  ```

  This inserts exactly one layer (workflow defaults) into today's chain, in
  the position the proximity rule dictates — a workflow saying "this pipeline
  runs in workspace x on a $2 budget" outranks the persona's stored defaults,
  just as a deployment's `run_options` outrank its agent today. The merged
  result is snapshotted onto the session as always; editing an agent or a
  workflow changes future runs only. The task stays a thin binding —
  `{who, what, after}` — prompts never move into agent docs, agent config
  never moves into tasks.
- **`depends_on` gives a DAG for free.** The default (`null` → previous task)
  keeps authoring linear — the `|`-pipe case needs zero extra syntax — while
  fan-out/fan-in is just naming dependencies (Databricks `depends_on`
  semantics: a task starts when all its dependencies succeeded; tasks with
  disjoint dependencies run in parallel).
- **The run doc is small, and orchestration-only.** The run doc is
  authoritative for one thing: which tasks may start (statuses + the
  advancement transaction). Sessions stay authoritative for content — journal,
  audit, cost. The only content the run doc ever copies is `result`, the
  task's final result text truncated to a hard cap (Databricks caps task
  values at 48 KiB; same order here), and it exists for prompt templating and
  display, not as a data bus.

## Structural invariants

The rules that keep the model clean as it grows; a change that breaks one of
these needs this doc updated first.

1. **One noun, one responsibility.** Agent = who. Workflow = what + when.
   Workspace = where. Session = one execution's content. Run = one firing's
   orchestration state. `AgentOptions` = the settings vocabulary all of them
   share. No noun stores another's data (the near-exception is the workflow's
   scheduler cursor — `next_run_at` and the display counters — which is the
   schedule's own state, same as deployments today).
2. **References point one way, by name, resolved at fire time.** Workflow →
   agent (per task); session → workflow/run/task (provenance); options →
   workspace. Nothing stores back-pointers or member lists — a workflow's
   run history is a query on its runs, an agent's usage is a query on
   sessions, exactly as workspace members are already derived, never stored.
3. **One merge rule.** Every defaults layer goes through the same per-field
   merge in the same proximity order. New layering needs (per-run parameter
   overrides, say) must join that chain, not invent a second mechanism.
4. **Every execution is a session.** There is no second execution primitive:
   a workflow run is N ordinary sessions plus one small orchestration doc.
   Anything sessions already give (approvals, audit, rewind, kill, tail)
   works on workflow tasks for free, and anything new added to sessions
   accrues to workflows without workflow code changing.
5. **Definitions are immutable to running work.** Options resolve and
   snapshot at session creation; a run captures its task list at launch.
   Editing an agent or workflow affects future runs only — no doc is ever
   read mid-run to decide what already-started work means.

## Passing data between tasks

Two channels, both explicit:

1. **Result templating** — `{{tasks.research.result}}` in a downstream prompt
   interpolates the upstream task's final result text. This is the direct
   analogue of the shell pipe, of Databricks `{{tasks.<n>.values.<k>}}`, and
   of Actions `needs.<job>.outputs`. No template → no implicit piping; tasks
   that want context ask for it. (Also available: `{{run.id}}`,
   `{{workflow.name}}`.) Named per-task output *values* (beyond the single
   result text) are an obvious v2 if result-text piping proves too coarse.
2. **Files** — tasks in one run share whatever `workspace` / `artifacts`
   spaces their options mount, which already exist and already checkpoint
   (Dagster's split: dependencies order execution, storage carries data).
   Guidance, not mechanism: a *linear* chain can share a `workspace` (the
   exclusive lease is harmless when tasks run one at a time); *parallel*
   branches must use artifact spaces (no lease; the workspace lease would
   serialize them — the trap `deployments.tick` already documents).

Deliberately not: Step Functions-style input/output path plumbing, or a
LangGraph-style shared typed state. Both buy parallel-branch power at a
complexity cost that a prompt-chaining platform doesn't need.

## Execution

Who moves a run forward? Same philosophy as the rest of syros — eager when
someone is around, reconciled by the clock, idempotent either way:

- **Launch** (`tick`, or `run_now`): create the run doc with every task
  `pending`, then start all root tasks — each start is `deployments.launch`
  as it exists today: create the session with provenance fields, push the
  (templated) prompt to the inbox, trigger the Cloud Run Job.
- **Advance** (runner, at session end): the runner already has a
  "session finished" moment where it writes title/summary and releases the
  lease (`runner.py`). A session carrying `workflow`/`run_id`/`task` also, at
  that moment: records its result + terminal status on the run doc
  (transaction), and launches every task whose dependencies just became
  all-succeeded. A failed task marks its downstream `skipped` and the run
  `failed`.
- **Reconcile** (`tick`): besides firing due schedules, the tick sweeps
  running runs and re-derives what should be running — a runner that died
  after finishing its session but before advancing is repaired at the next
  tick, using the same session-liveness checks (`lease_active`,
  `start_pending`) the deployment skip logic uses now. Advancement is a
  transaction on the run doc's task statuses, so runner and tick can race
  harmlessly.

Semantics, all inherited from today's deployments:

- **One active run per workflow** (`_run_active` generalizes to "the last
  run's status is running"): Databricks' `max_concurrent_runs = 1` default,
  and the only safe choice when tasks share a workspace.
- **Slots are claimed transactionally** (`claim_slot` unchanged), missed
  slots collapse to one catch-up firing, a bad cron parks the workflow with
  `last_error`.
- **Fail-fast, retry-whole-run**: v1 has no per-task retry; a failed run is
  re-fired manually. (Per-task `retries`/`timeout` are obvious later fields;
  every orchestrator grew them.)

## Surface changes

- `deployments.py` → `workflows.py` (build/create/get/list/enable/delete/
  run_now/tick keep their shapes; `launch` grows the per-task loop; new
  `advance(store, session)` called by the runner and the tick).
- `store.py` / `fakes.py`: the deployment CRUD block becomes the workflow
  CRUD block + run-doc CRUD + the advancement transaction. The three-way
  symmetry rule (StoreProtocol / Store / FakeStore) holds.
- `cli.py`: `syros workflows` replaces `syros deployments`
  (`create --cron ... --prompt ...` for the one-task case; `--tasks tasks.json`
  for chains; `run`, `runs`, `pause`, `resume`, `delete`). `syros tick` keeps
  its name and its Cloud Scheduler wiring.
- Console: `/api/workflows` routes mirror the deployment routes; the run view
  is a task list linking to each task's ordinary session page. `types.ts`
  mirrors the new shapes.
- Firestore: `deployments/*` documents are dropped, not migrated
  (experimental stage; the teams→workspaces read-through pattern exists if we
  regret this). Sessions' historical `deployment` field stays on old docs and
  is simply no longer queried.

## Industry survey

| product | persona/config | unit of work | definition (owns schedule?) | run | data between units |
|---|---|---|---|---|---|
| Databricks Jobs | cluster/env per task | Task | **Job** (yes: schedule, trigger, `max_concurrent_runs`) | Job Run → Task Runs | task values `{{tasks.<n>.values.<k>}}`, 48 KiB; `depends_on` |
| Airflow | operator/executor | Task | **DAG** (yes) | DagRun → TaskInstance | XCom, per-run scoped |
| Prefect | work pool | Task | Flow + **Deployment** (schedule on the Deployment) | Flow Run → Task Run | return values as arguments |
| Dagster | resources | Op / Asset | Job / asset graph (`@schedule` targets it) | Run | deps order only; storage carries data |
| GitHub Actions | runner | step → job | **workflow** (yes: `on:`) | workflow run | `needs` + declared `outputs`, producer-namespaced |
| Step Functions | — | State | State machine | Execution | Input/Output paths (cautionary tale) |
| Google Workflows | — | Step | Workflow (no — external Cloud Scheduler; disliked) | Execution | assigned variables |
| Temporal | worker | Activity | **Workflow** (schedules target it) | Workflow/Activity Execution | workflow code holds state |
| CrewAI | **Agent** (role/goal/backstory) | **Task** (refs an agent) | Crew + Process | kickoff → CrewOutput | sequential context / explicit `context=[...]` |
| OpenAI Agents SDK | **Agent** | run input / handoff | plain code | run result | handoffs (transfer) vs agent-as-tool (pipe) |
| LangGraph | node config | Node | StateGraph | invocation | shared typed State (overkill for chains) |

## Open questions

- Per-task `timeout` / `retries` — punt to v2?
- Should a run get an auto-mounted artifact space so chains have a scratch
  area without configuring one? Leaning no for v1 — explicit spaces keep the
  artifact list meaningful.
- `run_now` with parameter overrides (Databricks run-with-parameters) — v2,
  alongside `{{workflow.params.<k>}}` templating.
- Trigger kinds beyond cron + manual (fire on artifact change, webhook)? Far
  future; the `trigger` field on runs leaves room.
