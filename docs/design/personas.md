# User Personas (Revised) — syros Console

A revision of the original personas, corrected against the actual product. syros is not a
SaaS team-management console. It is a **minimal infrastructure/SDK product that runs
`claude_agent_sdk` agents sandboxed inside your own GCP project** (Cloud Run Jobs +
Firestore + GCS + IAM + Vertex AI). Auth, sharing, and tenancy are deliberately delegated
to GCP IAM (README, "Out of scope"), and the tenancy model is "one GCP project = one trust
boundary". These personas are grounded in that reality.

Subject: syros (SDK + console) · Date: August 2026 · Revised from the original — rationale
in [review-notes.md](review-notes.md).

> **Note:** The quantitative figures in each persona (session counts, tolerable
> interruption times, etc.) were stated without sourcing in the original and are **all
> hypotheses to be validated**. Anything marked _(assumption)_ needs confirmation through
> real user interviews.

---

## Devon Okafor — Builder (**primary persona**)

Senior software engineer · SDK-first power user

Delegates real work to agents from the SDK and runs several sessions in parallel. The
console is a secondary surface for monitoring, approvals, and intervention.

> "I want to stay in flow — let the agent handle the grind, and only pull me back in when
> it genuinely needs my judgment."

### Goals

- Hand off well-scoped tasks (refactors, tests, bug fixes, migrations) and trust agents to
  run them end to end.
- Keep 3–5 parallel sessions _(assumption)_ moving without losing track of any one of them.
- Respond to approval requests quickly — today, **an approval times out to a denial after
  300 seconds if nobody is watching** (`gate.py`), so a way to find out is critical.
- Redirect a running agent without restating context from scratch (this exists: follow-up
  queries to a live session).
- Hand work to teammates through artifact spaces (`artifacts/{space}/`) — sharing is done
  via IAM (`storage.objectViewer`).

### Frustrations

- **No way to be pushed a notification** for approvals or input requests — the console
  polls only, so an approval raised while the tab is closed silently falls to denial.
- **No search in the UI** for finding which past run touched a given file (state filter
  only).
- Stop reasons and state transitions (`workspace_busy` and friends) are rich, but noticing
  them depends on eyeballing the UI.

### Jobs to Be Done

> When I **hand a task to an agent**, I want to **be notified the moment it needs input and
> reply with just the decision**, so that **work isn't wasted on approval timeouts and I
> stay in flow**.

> When I **hand off output**, I want to share it through **artifact spaces plus read-only
> console access via IAP**. I don't want public share links — they cut against the trust
> boundary (one project = one boundary).

### Behaviors and habits (grounded in the real product)

- Entry point is the SDK (`query()` / `resume=`). The console is for monitoring and
  approvals.
- Uses `rewind` on the journal tree to branch from a past event instead of redoing work.
- Follows live runs with `syros tail`; reads diffs and artifacts rather than transcripts.
- Works around the one-run-per-workspace lease constraint (`workspace_busy`).

### Product needs

- Push notifications for input-needed / approvals (**top priority**)
- Session and transcript search
- Clear status indicators (already exists)
- Mid-run steering (already exists)
- resume / rewind (already exists)
- Visibility into approval timeouts

### Technical context

- Very high fluency. Uses the SDK, CLI, and BigQuery directly. Values speed and precision
  in the UI.
- Success = tasks delegated and completed, zero wasted stalls from approvals, flow
  preserved.

---

## Maria Chen — Operator

Fleet operator (GCP project owner) · rescoped from the original "engineering manager"

Accountable for the health and cost of every session running inside a single GCP project.
Organization-level "team budget allocation" is out of scope — there is no user model to
attribute spend to.

> "I don't need to watch every keystroke — I need to know the instant something goes
> sideways, and I need to get to the bottom of it in under two minutes."

### Goals

- See fleet health at a glance (already exists: `/` and `/dashboard` — state mix, daily
  spend, cost by model).
- Stop risky actions (force-push, secret exposure, prod access) before the fact, not after
  (foundations already exist: the approval gate plus the `syros kill` kill switch).
- Notice and act on sessions stuck in `stalled` (lease lapsed) or `queued`.
- Have a **project-level** spend ceiling — today only the per-query `max_budget_usd` exists,
  and the README itself states it "bound[s] a query, not a day" (README:371).

### Frustrations

- The dashboard answers her questions if she goes and looks, but **nothing pushes anomalies
  to her** — pull-only is the biggest hole.
- No daily/monthly spend ceiling or overrun alert, so a runaway session is invisible until
  she opens the dashboard.
- No fleet-wide way to find sessions that touched a given file or tool (the BigQuery export
  exists but is a snapshot).

### Jobs to Be Done

> When I **open the console during a check-in**, I want to **immediately see which sessions
> need me and why**, so I can **intervene only where it matters and let the rest run**.
> (The state model already exists — what's missing is push notification.)

> When I **manage spend**, I want **project-level daily/monthly ceilings with overrun
> alerts**, so I can **avoid finding out from the invoice**.

### Removed from the original (with reasons)

- **Per-engineer budget allocation and cost breakdown** — sessions have no owner (only
  `trigger` and `agent`). Unbuildable without a user model.
- **Defending a monthly team budget** — multi-env tenancy is explicitly out of scope. The
  project is the correct budget granularity.
- **The "no single pane of glass" pain point** — factually wrong; `/dashboard` already
  exists.

### Product needs

- Push notification for anomalies (**top priority**)
- Project-level budget ceiling + overrun alerts
- Fleet-wide search
- Session auto-summary
- Dashboard (already exists)
- Kill switch (already exists)

### Technical context

- High fluency but time-constrained. Wants a few check-ins a day _(assumption)_ plus
  alert-driven interruption.
- Success = no surprises, under two minutes from detection to root cause _(assumption)_,
  landing within budget.

---

## Priya Nair — Auditor

Security and compliance reviewer · audits using the journal, BigQuery, and IAM

Proves what an agent actually did, independent of the agent's own account of it. syros's
journal already provides most of this — the remaining hole is attribution of *who
approved*.

> "I don't care what the agent **meant** to do — I care what it actually did, whether it
> was allowed to, and whether I can prove that six months from now."

### What she already has (the original wrongly claimed these were missing)

- **An audit ledger committed before execution**: the `PreToolUse` hook commits a
  `tool_call` record to Firestore **before** the tool runs (`journal.py`, `gate.py`). This
  is a primary record, independent of the agent's narrative.
- **Complete approval records**: the approval queue records every decision (allow / deny /
  timeout).
- **Cross-fleet analysis in SQL**: `syros export` → five BigQuery tables (`sessions`,
  `events`, `tool_calls`, `approvals`, `agents`). "Which sessions touched credentials.json
  this month" is expressible here.
- **Read-only access**: delivered through IAM viewer roles / IAP invitation rather than an
  in-app role, exactly as designed.

### Frustrations (the real ones)

- **The attribution hole (most important)**: approval decisions made from the console are
  recorded as `getpass.getuser()` or the literal string `"console"` rather than the IAP
  identity (`_decided_by()` at `src/syros/console/api.py:163`). Until "who authorized this"
  is provable, the audit-trail claim doesn't hold.
- The BigQuery export is a **snapshot** — anything after the last export is invisible to
  SQL.
- No fleet-wide search in the UI, so every scheduled sweep means writing SQL.
- No automatic flagging of policy violations (secret patterns and the like) — detection is
  manual querying.

### Jobs to Be Done

> When I **run a scheduled audit or respond to an incident**, I want **a complete record of
> privileged actions with the identity of both the executor and the approver**, so I can
> **prove nothing improper happened — or pinpoint it immediately if it did**. (The record
> exists; the approver's identity is what's missing.)

### Product needs

- IAP identity attribution for approval decisions (bug fix, **top priority**)
- Fleet-wide search in the UI
- Automatic flagging of policy patterns
- Fresher or scheduled exports
- Audit ledger (already exists)
- Read-only access via IAM viewer (as designed)

### Removed from the original (with reasons)

- **An in-app audit role hierarchy** — auth is delegated to IAM/IAP by design (README, "Out
  of scope"). The requirement should be written in IAM's vocabulary.
- **Assumed SOC2-style organizational compliance workflow** — overscoped for a
  single-project trust boundary. Review-status tracking should be reconsidered as
  lightweight tagging.

---

Personas are living documents — validate the figures marked _(assumption)_ through real
user interviews and update them. Revision rationale: [review-notes.md](review-notes.md)
