---
name: pre-mortem
description: Run a pre-mortem — assume the plan already failed and work backwards to the causes, then turn each one into a risk with an owner and a tripwire. Use when asked for a pre-mortem, a risk register, "what could go wrong", or a review of a plan before it is committed to.
---

# Pre-mortem

The method, from Gary Klein: do not ask "what could go wrong". Ask a harder
question that people can actually answer.

> It is <a date after the plan was due>. The plan failed — not partially, not
> "we learned a lot". It failed, publicly, and everyone agrees it failed. Write
> the story of how.

The reframe is the whole technique. "What could go wrong" invites hedged,
low-status guesses that everyone nods at and nobody records. Certainty of failure
gives permission: the failure is now a fact to be explained, not a prediction to
be defended, so the objection someone has been sitting on becomes the obvious
answer rather than an act of disloyalty.

## Method

1. **Fix a date and a definition of failure.** Both concrete. "Q3 was
   disappointing" is not failure; "we shipped in September and the two launch
   partners had both left by then" is.
2. **Write the story, not a list.** Causes in a list are independent and each one
   looks survivable. Causes in a narrative compound, which is how failure
   actually arrives: the dependency slipped, *so* the integration work landed in
   the same week as the freeze, *so* the fix went out unreviewed.
3. **Work backwards to the first controllable moment.** For each cause: what is
   the earliest point at which someone could have done something? That moment,
   not the failure, is the thing to write down.
4. **Separate what you would bet on from what you fear.** Give each cause a rough
   likelihood, and say which are your top two. A register where everything is
   "medium" is a register nobody will triage.
5. **Convert each cause into a risk with an owner and a tripwire.** This is the
   step that gets skipped, and skipping it is what turns a pre-mortem into a
   memorable meeting with no consequences.

## What makes a risk real

A risk that is a *worry* has no owner and no tripwire, and its only possible
resolution is the thing happening. A risk that is real has:

- **An owner.** One name. Shared ownership of a risk is the absence of ownership.
- **A tripwire.** The specific observable event that means it is now happening —
  something someone would notice without being told to watch for it. "If adoption
  is slow" is a worry. "If fewer than ten teams have migrated by 1 March" is a
  tripwire.
- **A response decided in advance.** What we do when it fires. Deciding under
  pressure is exactly what the register exists to avoid.

## Lenses

A group asked to imagine failure will produce four versions of the same fear.
Assign different angles deliberately — the useful ones are usually:

- **Dependency** — what we do not control: another team, a vendor, a review, a
  hire, a decision that has not been made yet.
- **Adoption** — the plan works and nobody uses it. Who has to change their
  behavior, and what happens if they simply do not.
- **Operational** — it works and then breaks: what it costs to run, who is on
  call for it, what it does at ten times the load, and what happens the first
  time it fails at 3am.

Each lens is genuinely blind to the others; that is why they are run separately
rather than by one pass asked to "consider all angles".

## What to avoid

- Failures nobody in the room can influence (the company is acquired, the market
  turns). True, useless, and they crowd out the causes that have owners.
- Restating the plan's known hard parts as risks. If it is on the roadmap as
  difficult, it is work, not a risk.
- Ending with a register and no changes to the plan. A pre-mortem that changes
  nothing was a performance. Say plainly which risk should change the plan now,
  and which are only worth watching.
