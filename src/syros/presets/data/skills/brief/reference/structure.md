# Brief structure

The section layout the `brief` skill produces. Sections are dropped when they
would be empty — an empty heading is worse than a missing one.

```markdown
# <subject> — <date>

<Lead. One paragraph. What changed, what it means, what it forces.>

## What changed

- <Fact, with its source inline.> ([source](url), 2026-03-04)
- <Fact.> ([source](url), 2026-03-02)

## Why it matters

<Two or three sentences of consequence, specific to this reader. Not "this is
significant for the industry" — significant to whom, and what does it cost them
to ignore it.>

## What we don't know

- <The open question, and what would answer it.>

## Recommendation

<One action, with the tradeoff it accepts. If the honest recommendation is
"nothing yet", say that and name the trigger that would change it.>
```

## A worked lead

Weak — a summary pretending to be a brief:

> This brief covers recent developments in sandboxed agent execution. Several
> vendors have released new features in this space, and there has been
> significant discussion about isolation models. Overall the area is evolving
> rapidly and is worth continued monitoring.

Nothing in that paragraph is false and nothing in it is usable. No specific
change, no consequence, no decision.

Strong:

> Two of the three runtimes we evaluated in January now default to denying
> outbound network access from the agent sandbox, which breaks our assumption
> that egress filtering had to be built at the VPC layer. That removes roughly
> a quarter of the isolation work from our own roadmap, but only if we accept
> their per-run credential model — which we rejected in January for reasons
> that still hold. The decision is whether to revisit that rejection now or
> stay on the VPC path.

Same subject, one paragraph, and the reader knows what is being asked of them.

## On dates

Every brief carries the date it was written in its title, and every source
carries the date it was published. A brief without dates cannot be re-read six
months later, which is exactly when someone will re-read it.
