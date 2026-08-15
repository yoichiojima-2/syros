# syros console frontend

Next.js 15 (static export) + React 19 + TypeScript + Tailwind 4, with
shadcn-style components, next-themes (light/dark), and recharts.

Pages: `/` (overview: stat tiles, cost chart, recent sessions), `/dashboard`
(analytics: spend and activity per day, state mix, cost by model), `/sessions`
(list + new-session form), `/session?sid=...` (transcript, composer, approvals,
artifacts), `/approvals` (global pending queue), `/agents` + `/agent` (stored
personas), `/workflows` + `/workflow` (task chains, cron runs, outcome/duration
bars), `/connectors` (catalog + credential status), `/workspaces` +
`/workspace` (shared working directories, file editing), `/artifacts` (shared
spaces), `/skills` + `/skill` (bucket-backed Agent Skills: drop a skill
directory to create one, then edit its files in place), `/settings` (global
option defaults). Endpoints live in `src/syros/console/api.py`.

The session page includes a Claude-style artifact panel: files the agent
writes are reconstructed client-side by replaying Write/Edit tool calls from
the transcript (`src/lib/artifacts.ts`) — no workspace API needed. HTML, mark-
down, and SVG preview in sandboxed iframes with per-file version history.

- `npm run dev` — dev server; proxies `/api` to `http://127.0.0.1:8484`, so run
  `syros console --no-open` alongside.
- `npm run build` — static export copied into `../src/syros/console/static/`.

The build output is gitignored — it used to be committed and conflicted on
nearly every merge. The Docker image builds the frontend in its own Node
stage, so deploys are unaffected; for a local `syros console`, run
`make console` from the repo root once (and again after frontend changes).
