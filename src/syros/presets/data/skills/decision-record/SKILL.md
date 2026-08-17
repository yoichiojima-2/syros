---
name: decision-record
description: Write a decision record that stays useful after the people who wrote it have left — the question, the options, the assumption it rests on, and the tripwire that would reopen it. Use when recording a decision, writing an ADR, documenting a choice between options, or capturing what a group agreed and why.
---

# Decision record

A decision record is written for a stranger in eighteen months who is about to
undo your decision. They do not need to be persuaded; they need to know what you
knew, what you assumed, and what would have changed your mind. Write for them and
the record is also good for everyone else.

The failure mode is not a badly written record. It is a record that documents the
*outcome* and loses the *reasoning* — "we chose Postgres" — which reads as
arbitrary the moment the context shifts, and gets overturned by someone who does
not know they are re-running an argument that was already had.

## The five things

1. **The question**, as a question. If you cannot phrase it as one, you are
   recording a task, not a decision.
2. **The options**, including the one you rejected — stated as its own advocate
   would state it, not as the loser in a comparison table. A record with one
   option is a minute.
3. **The decision**, and the tradeoff it accepts. Every real decision costs
   something. Naming the cost is what distinguishes a decision from a preference.
4. **The assumption it rests on.** Not the reasons — the belief about the world
   that, if false, makes this the wrong call. This is the field people skip and
   the field that pays for the whole document.
5. **The tripwire.** The specific observable event that means "reopen this". It
   must be something someone could notice without being told to look. Read
   `reference/template.md` for the difference between a tripwire and a worry.

Then, when they exist: **who disagreed, and with what**. A record with no
dissent recorded is either a decision nobody cared about or a document that
smoothed one over. Both are worth knowing.

## Writing it

- **Name the file for the question, not the meeting.** `decisions/vendor-vs-build-auth.md`,
  not `decisions/2026-03-04-architecture-sync.md`. Nobody searches by date.
- **Present tense, active voice, no minutes.** "We route writes through the
  primary" — not "it was agreed that writes would be routed".
- **One page.** If the analysis genuinely does not fit, it goes in `notes/` and
  the record links to it. A decision record that grew into a report has stopped
  being a record.
- **Write it when the decision is made, not when the work ships.** A record
  written afterwards is reconstruction, and reconstruction is where the
  alternatives quietly disappear.

## Superseding

Decisions are not edited into new decisions. Write a new record, link back to the
old one, and add a line at the top of the old one pointing forward. The old
reasoning is the most valuable thing you have when the new decision goes wrong —
and the workspace checkpoint never deletes blobs anyway, so an edit-in-place just
loses the history without saving the file.

## What to avoid

- Listing options you never seriously considered, to make the process look
  thorough. It makes the record longer and less honest.
- Hedging the decision itself. The record says what was decided; uncertainty
  belongs in the assumption and the tripwire, where it is actionable.
- "We will revisit this as needed." That is the sentence a tripwire replaces.
