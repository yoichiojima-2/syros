# Decision record template

The layout the `decision-record` skill produces. Sections are dropped when they
would be empty — an empty heading is worse than a missing one — except
**Assumption** and **Tripwire**, which are the two the `retro` workflow reads. A
record without them is invisible to it.

```markdown
# <the question, as a question> — <date>

**Decision:** <one sentence. What we are doing.>

## The question

<Two or three sentences. What forced the choice, and what is at stake in getting
it wrong. Not a project summary.>

## Options

### <Option A> — chosen

<Stated as its advocate would state it. Then: the tradeoff it accepts.>

### <Option B> — rejected

<Also stated as its advocate would state it. Then: why not, specifically. "More
complex" is not a reason; "adds a second on-call rotation we cannot staff until
Q3" is.>

## Assumption

<The belief about the world that makes the chosen option right. If this is false,
the decision is wrong — not merely suboptimal.>

## Tripwire

<The observable event that means reopen this record. Include how anyone would
notice it, and by when.>

## Disagreement

- <Who objected, to what, and on what grounds. Unresolved is a fine state to
  record.>

## Sources

- <What this was decided from.> ([source](url), 2026-03-04)
```

## Tripwires and worries

A worry is a feeling about the future. A tripwire is a thing that either happens
or does not, that someone will notice without being asked to look for it.

| worry | tripwire |
|---|---|
| "if adoption is slow" | "if fewer than ten teams have migrated by 1 March" |
| "if costs get out of hand" | "if monthly spend passes $8k, or doubles month over month" |
| "if the vendor gets flaky" | "if we hit a second Sev2 attributable to them in one quarter" |
| "if the team finds it painful" | "if it comes up unprompted in two consecutive retros" |

The test: could someone who has never read this record trip the wire? If noticing
it requires already believing the risk, it is a worry.

## A worked assumption

Weak — a reason wearing an assumption's clothes:

> We assume this is the best approach given our current constraints and
> priorities.

That is unfalsifiable, so nothing can ever contradict it, so the record can never
be reopened by evidence.

Strong:

> We assume the compliance review that blocked the hosted option in January is
> permanent. If it is relitigated — the security team indicated they would revisit
> it after the SOC 2 audit closes — the hosted option is cheaper than what we are
> building and this decision should be reversed rather than defended.

Same belief, but now it names the event that would falsify it, and says what
follows. That second sentence is what makes a record worth keeping.
