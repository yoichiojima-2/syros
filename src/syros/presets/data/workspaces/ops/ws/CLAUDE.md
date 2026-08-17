# ops

The organization's memory. Every session created with `AgentOptions(workspace="ops")`
gets this directory as its working directory and this file as project memory — you are
reading it because the runner passes `setting_sources=["project"]`.

Edit this file freely. It is yours now; nothing overwrites it.

What lives here is not status. Status expires and nobody re-reads it. What lives here
is the reasoning behind decisions, the risks someone actually named, and the verdicts
of coming back later to see who was right.

## Layout

- `decisions/` — one record per decision, `kebab-case.md`, named for the question and
  not for the meeting. Format: the `decision-record` skill.
- `risks/` — one register per plan, from `risk-register` runs. Format: the
  `pre-mortem` skill.
- `retros/` — what the `retro` workflow found when it came back: which tripwires
  fired, which assumptions held, and which decisions nobody revisited.
- `notes/` — raw material. Anything a record was built from, with its source and date.
  Also where a worry lands: something with no owner and no tripwire is not a risk, and
  putting it in `risks/` anyway is how a register stops being triaged.
- `faq.md` — the questions people keep re-asking, maintained by the `faq` workflow.
  Rewritten in place each week, never appended to.
- `artifacts/ops/` — the shared artifact space, mounted when the session's options ask
  for it. It has no lease, so it is the only channel between tasks that run at the
  same time: the `advocate` and `contrarian` branches publish their cases here, and
  `recorder` reads them. Published, not private — treat it as readable by anyone with
  bucket access.

## House rules

These are conventions the workflows depend on, not style preferences. A record that
breaks them still installs; it just stops being usable six months later, which is the
only time anyone opens it.

- **Every decision names the option it rejected.** A record with one option is a
  minute, not a decision. If there genuinely was no alternative, write that down —
  it is the most interesting sentence in the file.
- **Every decision names the assumption it rests on.** Not the reasons — the belief
  about the world that, if false, would make this the wrong call.
- **Every decision and every risk carries a tripwire**: the specific, observable thing
  that means "reopen this". "If adoption is slow" is not a tripwire. "If fewer than
  ten teams have migrated by March" is. The `retro` workflow reads exactly this field;
  a record without one is invisible to it.
- **Record the disagreement.** Who objected, and to what. Consensus written down after
  the fact is how an organization forgets it ever had a choice.
- **Attribute nothing to a person that they did not say.** Positions get recorded, and
  so do names when the source is written and checkable. Anything reconstructed from
  memory is unattributed.
- **Dates on everything.** Every record carries the date it was written; every claim
  taken from somewhere carries that source and its date.
- **Rewrite files; do not delete them.** The workspace checkpoint only uploads — it
  never removes blobs — so a file deleted inside a run reappears on the next one.
  Superseding a decision means a new record that links back, plus a line at the top of
  the old one pointing forward. The history is the point.
- **One session holds this workspace at a time** (it takes an exclusive lease). Work
  that needs to run in parallel belongs in `artifacts/ops/`, which has none.
