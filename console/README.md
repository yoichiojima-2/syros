# syros console frontend

Next.js 15 (static export) + React 19 + TypeScript + Tailwind 4, with
shadcn-style components, next-themes (light/dark), and recharts.

Pages: `/` (overview: stat tiles, cost chart, recent sessions), `/sessions`,
`/session?sid=...` (transcript, composer, approvals), `/approvals` (global
pending queue). Endpoints live in `src/syros/console/api.py`.

- `npm run dev` — dev server; proxies `/api` to `http://127.0.0.1:8484`, so run
  `syros console --no-open` alongside.
- `npm run build` — static export copied into `../src/syros/console/static/`.

The build output is committed so the Python package is self-contained — no Node
toolchain needed for `pip install` or the Docker image. After changing the
frontend, run `make console` from the repo root and commit the regenerated
static files.
