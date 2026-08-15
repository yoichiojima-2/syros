# research

The shared workspace behind the example agents and the `research-pipeline`
workflow. Every session created with `AgentOptions(workspace="research")` gets
this directory as its working directory and this file as project memory — you
are reading it because the runner passes `setting_sources=["project"]`.

Edit this file freely. It is yours now; nothing overwrites it.

## Layout

- `notes/` — raw material. What you read, quoted, with the URL and the date.
- `reports/` — finished pieces, one file per topic, `kebab-case.md`.
- `artifacts/research/` — the shared artifact space, mounted when the session's
  options ask for it. Anything here is published: other sessions and other
  workflow runs read it. Deliverables go here; working notes do not.

## House rules

- Every claim that came from a source carries that source inline. If there is
  no source, write "unverified" rather than asserting it.
- Lead with what changed and why it matters, then the evidence. A reader who
  was not in the room should get the point from the first paragraph.
- **Rewrite files; do not delete them.** The workspace checkpoint only uploads —
  it never removes blobs — so a file you delete inside a run reappears on the
  next one. If something is wrong, overwrite it with the correct content.
- One session holds this workspace at a time (it takes an exclusive lease). If
  you need work to run in parallel, that work belongs in an artifact space
  instead, which has no lease.
