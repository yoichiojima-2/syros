# Review Notes: Critical Review of the Original Personas / Feature-Recommendation Docs

A record of what came out of checking the original `personas.html` /
`featurerecommendations.html` (August 2026, authored by UX Design) against the syros
codebase (v0.2.0), and how the revised versions were corrected.

## Overall assessment

The original was written without consulting the codebase and reads close to a generic
"AI agent SaaS console" template. Three systematic errors run through it:

1. **Fictional gaps** — at least 5 of the 17 "ADD" items are already implemented.
2. **Misdescription of current state** — 2 of the "MODIFY" items present existing behavior
   as a proposal.
3. **Conflicts with stated design** — several items contradict the README's "Out of scope"
   section and the "one GCP project = one trust boundary" position.

## What the product actually is (the baseline for comparison)

- A minimal infrastructure/SDK product that runs `claude_agent_sdk` agents sandboxed in GCP
  Cloud Run Jobs. Just a Python package (`src/syros/`) and Terraform (`infra/`). No REST
  API, no always-on cost.
- Firestore = session state, journal, approval queue / GCS = workspaces and artifacts /
  IAM = auth / Vertex AI = model access.
- The console is a static Next.js export served by a stdlib HTTP server, and it polls.
- The README's "Out of scope" list (REST API, versioned registry, vaults/egress proxy,
  multi-env tenancy) is the design boundary.

## Verified findings

### Already implemented, but listed as "ADD"

| Original item | Where it lives |
|---|---|
| Fleet-wide dashboard | `/`, `/dashboard` (`console/src/app/dashboard/page.tsx`) |
| Tamper-evident action ledger | `journal.py`, `gate.py` — `PreToolUse` commits before execution; `analytics.py` exports to BigQuery |
| Mid-run steering | Follow-up queries via the inbox, composer during a live run |
| Session resume / continuation | `resume=`, journal tree with `rewind` branching |
| Jump-to-error equivalent | Partially covered by typed journal records + `state-badge` |

### "MODIFY" items that misdescribe the current state

- **Status model, "binary → richer"**: the current model is
  `running|starting|stalled|queued|idle|terminated|unknown` plus an orthogonal `RunOutcome`
  (`console/src/lib/types.ts`, `derived_state()` in `console/api.py`). The requested
  "possibly-stalled" ships as lease-based `stalled`.
- **Approvals, "real-time interrupt → async queue"**: already an async Firestore queue,
  audited, with a 300s timeout-deny (`gate.py`). Only risk-tiering is new.

### Items that conflict with the design (dropped or reframed)

- In-app audit roles → IAM viewer / IAP invitation (`console_iap` in `infra/main.tf`).
- Shareable session links → reframed around artifact spaces + `storage.objectViewer` + IAP.
- Per-engineer budgets → sessions have no owner concept (only `trigger` / `agent`).
  Unbuildable.

### The genuine gaps (raised in priority in the revision)

1. **No notifications (biggest).** Zero push mechanism. An unattended approval times out to
   a *denial* after 300 seconds.
2. **Attribution bug.** `_decided_by()` (`src/syros/console/api.py:163`) records
   `getpass.getuser()` / `"console"` instead of the IAP identity, hollowing out the
   audit-trail claim. Confirmed in use at approval decisions (`api.py:302`), session
   creation (:239), deployment creation (:447), and elsewhere.
3. **No search.** The UI has only a state filter; the BigQuery export is a snapshot.
4. **No fleet-level budget ceiling.** Only per-query `max_budget_usd` (acknowledged at
   README:371).

### Persona corrections

- **Devon (Engineer)** → promoted to primary persona; closest to the real user (SDK-first).
  The sharing JTBD was rewritten in IAM / artifact-space terms.
- **Maria (Manager)** → rescoped as "fleet operator (project owner)". The per-engineer
  budget material (roughly 40% of the original) was removed.
- **Priya (Security)** → grounded in the journal, BigQuery, and IAM, with the attribution
  bug as her top pain point. The assumed SOC2-style organizational workflow was removed.
- All unsourced quantitative figures were labeled as assumptions to validate.

## Deliverables

- `docs/design/personas.md` — revised personas
- `docs/design/feature-recommendations.md` — corrected gap analysis
- This file — the record of why each change was made
