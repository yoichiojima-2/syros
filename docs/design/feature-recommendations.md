# Feature Recommendations (Corrected) — Verified Against the Codebase

A correction of the original feature-gap analysis, checked against the syros codebase
(README, `src/syros/`, `console/src/`). Of the original's 17 "ADD" items, **at least 5 are
already implemented**; 2 of the 8 "MODIFY" items describe current behavior as if it were a
proposal; and several items contradict the design positions stated in the README's "Out of
scope" section (IAM delegation, single-project trust boundary). This version moves the
already-built items into their own section and re-prioritizes only the genuine gaps.

Companion to [personas.md](personas.md) (revised) · Date: August 2026 · Rationale:
[review-notes.md](review-notes.md)

## Summary

| Category | Count |
|---|---|
| Already exists (original mislabeled as ADD/MODIFY) | 6 |
| Genuinely worth adding | 6 |
| Existing functions to change | 3 |
| Dropped / reframed (conflicts with stated design) | 5 |

## Already exists — implemented functions the original called "net-new"

Remove these from the roadmap. All references are to current code.

| Function | Where it lives | Notes |
|---|---|---|
| Fleet-wide dashboard | `console/src/app/dashboard/page.tsx`, plus `/` | Stat tiles, daily spend/activity, state mix, cost by model, 24h/7d/14d range toggle. |
| Tamper-evident action ledger | `journal.py`, `gate.py`, `analytics.py` | The `PreToolUse` hook commits a `tool_call` record **before** the tool runs. Exportable to BigQuery. A core selling point. |
| Rich session status model | `console/src/lib/types.ts`, `derived_state()` in `console/api.py` | `running/starting/stalled/queued/idle/terminated/unknown` plus an orthogonal `RunOutcome`. The "possibly-stalled" state the original asked for ships as `stalled` (lapsed lease). |
| Async approval queue with full logging | `gate.py`, the `/approvals` page, `approval-card.tsx` | Firestore queue + audit records + 300s timeout-deny. The original's MODIFY item ("real-time interrupt → async queue") describes what already exists. |
| Mid-run steering | Follow-up queries via the inbox, console composer | A `query()` into a live session lands in its inbox. |
| Session resume and branching | `resume=`, journal tree + `rewind` | Supports branching from any past event — stronger than what the original requested. |

## Add — the genuine gaps (re-prioritized)

Absent from the codebase and consistent with the stated design.

| # | Function | Persona(s) | Priority | Why |
|---|---|---|---|---|
| 1 | Push notifications for approvals / input-needed (email, webhook, Slack) | Engineer, Operator | **High** | **The single biggest real gap.** The console polls and has no notification mechanism at all. An approval times out to a **denial** after 300 seconds (`gate.py`) — failing to notice causes direct harm. |
| 2 | IAP identity attribution for approval decisions (bug fix) | Security | **High** | `_decided_by()` (`src/syros/console/api.py:163`) falls back to `getpass.getuser()` or the literal `"console"`. It should read the authenticated IAP header. Until this is fixed, "who approved this" is unprovable, which undermines the value of the audit ledger. |
| 3 | Session and transcript search in the UI | Security, Operator, Engineer | **High** | The UI has only a state-chip filter (`state-filter.tsx`). "Which sessions touched credentials.json" is answerable only through the BigQuery export, which is a snapshot. |
| 4 | Project-level budget ceilings + overrun alerts | Operator | **High** | Only the per-query `max_budget_usd` exists. README:371 concedes it "bound[s] a query, not a day — use a BigQuery custom quota for a hard ceiling." Daily/monthly ceilings and threshold alerts should be scoped to the project, not per engineer (there is no user model). |
| 5 | Session auto-summary | Operator, Engineer | **Med** | Helps skim long journals. A convenience layer over the ledger, never a replacement for the primary record. |
| 6 | Risk-tiered approval rules | Engineer, Security | **Med** | The only genuinely new part of the original's MODIFY item. A coarse version already exists via `allowed_tools`/`permission_mode`, so this is an extension, not a new capability. Re-evaluate its value after (1) ships. |

## Modify — changes to existing functions

What remains of the original's 8 items after removing the 2 that misdescribe current
behavior and the rows that presuppose features which don't exist.

| Function | Current → Change | Persona(s) |
|---|---|---|
| BigQuery export freshness | Manual `syros export` snapshots → scheduled runs (reusing the deployment mechanism) or incremental export, so audit queries stay current | Security |
| Approval timeout handling | Silent denial at 300s → surface imminent and elapsed timeouts in session detail and in notifications (Add-1). Per-session timeout configuration should follow the existing `AgentOptions` pattern | Engineer |
| Transcript viewer | Flat rendering → collapse routine records / highlight privileged tool calls, using the journal's typed records directly | Operator, Security |

## Drop / reframe — items that conflict with the stated design

Based on the README's "Out of scope" section and the "one GCP project = one trust boundary"
position.

| Original item | Why it is dropped or reframed |
|---|---|
| In-app read-only audit role / access tiers | Auth is delegated to IAM/IAP by design (`console_iap` / `console_invokers` in `infra/main.tf`). The right answer is documenting an IAM viewer role plus IAP invitation, not building an in-app role model. |
| Shareable session links + export to Slack/PR | Public share links cut against the trust-boundary model. Sharing in practice means artifact spaces (`artifacts/{space}/` + `storage.objectViewer`) and console access through IAP. "Read-only sharing" should be reframed in those terms. |
| Per-engineer budget allocation and cost breakdown | There is no user model to attribute spend to (sessions carry `trigger` and `agent`, not an owner). Unbuildable as specified. Budgets belong at project granularity (Add-4). |
| Role-split alert streams | Presupposes in-app roles that don't exist. Build the notification itself first (Add-1); routing can follow as a notification setting. |
| Consolidating duplicate cost pages | The premise is factually wrong — cost display is already unified in `/dashboard` and no duplicate pages exist. |

## Keep — what the original got right

**Don't treat the agent's own narrative as the sole audit evidence.** Valid — and syros is
already built this way. The `tool_call` record is a primary record committed by a
platform-side hook before execution, independent of anything the agent says about itself.
Summaries (Add-5) should stay a convenience layer backed by the ledger.

---

Prioritization reflects a comparison against codebase v0.2.0 as of August 2026. Rationale
for each judgment: [review-notes.md](review-notes.md)
