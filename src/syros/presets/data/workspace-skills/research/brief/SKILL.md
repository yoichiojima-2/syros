---
name: brief
description: Write a decision-ready brief, following the research workspace's conventions for where files go and how sources are recorded. Use when asked for a brief, a status update, a summary of developments, or a short report someone will act on.
---

# Brief (research workspace)

**This skill shadows the global `brief` skill.** Both are named `brief`; the
runner mounts global skills first and this workspace's skills over them, so
inside the `research` workspace this file is the one that loads and the global
one is invisible. That is the whole mechanism — a workspace skill overrides a
same-named global by name alone, with nothing to configure.

Everything in the global skill still applies: find the delta first, lead with
the change and its consequence, attach evidence to individual claims, mark
inference as inference, and keep it to one page. What follows is what this
workspace adds.

## Where things go

- Finished briefs: `reports/<topic>.md`, kebab-case, one file per topic. A
  second brief on the same topic **rewrites** that file — the workspace
  checkpoint never deletes blobs, so accumulating dated copies leaves a mess no
  session can clean up.
- Raw material: `notes/<topic>.md`, with the URL and retrieval date on every
  excerpt.
- Anything another run needs to read: `artifacts/research/`. That space has no
  lease, so it is the only safe channel between tasks that run in parallel.

## Sources in this workspace

Inline citations carry the retrieval date as well as the publication date:

```markdown
- Vertex now pins the model at job submission, not per request.
  ([docs](https://example.invalid/vertex), published 2026-02-11, read 2026-03-04)
```

The retrieval date matters here because these briefs are produced by scheduled
runs — a claim sourced from a page that has since changed needs to be
distinguishable from one read this morning.

## Before you finish

Re-read the brief against the workspace house rules in `CLAUDE.md`. The
`reviewer` agent checks exactly those rules and it cannot edit, so anything it
finds costs a whole extra run to fix.
