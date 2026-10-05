# FACTORY.md — a four-seat software factory in Band Desktop

This document is enough to stand the factory up on a different problem: what each seat
owns, how it is configured, why it is shaped this way, what it cost, and how it catches and
recovers from bad work. The mandates in `mandates/` are the seats' standing instructions and
contain nothing specific to this track; the track detail lives only in the task pasted into
the room at each stage.

## 1. Shape

Four seats, one room, one result repository, one human message per stage.

```
                 ┌──────────────┐
   human ──────▶ │ coordinator  │  plans, routes, accepts, writes reports/stage-N.md
   (1 msg/stage) └──┬───────┬───┘  owns no code
                    │       │
         build      │       │   probe handoff (same requirements, in parallel)
         handoff    ▼       ▼
          ┌──────────────┐ ┌──────────────┐
          │ implementer  │ │   auditor    │  derives black-box probes from the
          │ builds the   │ │   never reads│  requirements BEFORE seeing any code
          │ service      │ │   impl first │
          └──────┬───────┘ └──────┬───────┘
                 │ revision        │ probes
                 ▼                 ▼
              ┌──────────────────────┐
              │       reviewer       │  clean container, official harness, auditor
              │  accept / reject     │  probes, hand checks, code read, verdict
              └──────────┬───────────┘
                         │ reject → back to implementer / auditor, new commits only
                         ▼
                 coordinator accepts only an explicitly reviewed revision
```

Why four and not three: the toy template (coordinator / implementer / reviewer) lets the
reviewer be the only source of checks beyond the shipped samples, and the reviewer is busy.
Splitting out an **auditor** that writes probes from the requirements alone, before the
implementation exists, gives the reviewer an independent, requirement-derived suite to run,
and makes "what the shipped checks never asked" somebody's whole job. It also makes the
work visibly distributed: probes are code, committed by a different seat than the service.

Why the auditor must not read the implementation first: expectations derived from the code
reproduce the code's assumptions. Expectations derived from the text catch the gap between
them. The stage-2 rejection below is exactly that gap.

## 2. Seat setup

| Seat | Runtime | Model (stage 1) | Model (stage 2–3) | Model (stage 4) | Effort | Permission mode | Working directory |
|---|---|---|---|---|---|---|---|
| coordinator | Claude Code | claude-fable-5-1 | claude-fable-5-1 | claude-sonnet-5 | high | Auto | `C:\Users\<user>\band-work\result` |
| implementer | Claude Code | claude-fable-5-1 | claude-opus-5-5 | claude-opus-5-5 | high | Auto | same |
| auditor | Claude Code | claude-fable-5-1 | claude-opus-5-5 | claude-opus-5-5 | high | Auto | same |
| reviewer | Claude Code | claude-fable-5-1 | claude-fable-5-1 | claude-opus-5-5 | high | Auto | same |

Each seat is created in Band Desktop with **Create your own**: name = the mandate filename
stem, role = the mandate file, runtime = Claude Code, permission mode = Auto (an unattended
run cannot stop for approvals), working directory = the result repository so commits land
where the harness reads them. All seats share one Claude login. The event harness runs in
WSL Ubuntu; the reviewer and implementer invoke it from Windows via
`wsl -d Ubuntu -- bash -lc "..."`, a command given in the task, not the mandate.

The model changes between stages were deliberate cost decisions, each committed to
`mandates/` before the next dispatch so the `Model:` line always names what the seat ran:

- after stage 1, implementer and auditor moved from fable to opus: stage 1 alone consumed
  roughly 70% of a five-hour usage window on four frontier seats;
- before stage 4, all seats left fable: the weekly fable allowance was down to 9%; the two
  judgment-heavy seats (implementer, reviewer) and the auditor went to opus, the coordinator
  — which routes, pastes and reports — to sonnet.

## 3. How a stage runs

1. The human pastes one dispatch to `@coordinator`: paths, stage number, the spec file
   locations, the exact harness command, the clean-container command, and what "done" means.
2. The coordinator reads the whole spec, confirms all seats are in the room, and sends
   **two handoffs in parallel**, each containing the complete requirements text verbatim:
   build → implementer, probes → auditor.
3. For stages after the first, the implementer's first commit is the previous stage folder
   copied forward **unchanged**; the coordinator verifies the tree diff is empty before any
   extension begins. Earlier folders are never touched again.
4. Implementer and auditor each hand off a committed revision with the commands they ran and
   their results. The coordinator verifies each independently (clean tree, HEAD matches,
   harness report at that revision) before routing.
5. One review handoff to the reviewer: requirements, revision, probe location, commands.
   The reviewer rebuilds from a clean tree, runs the official harness, starts the container
   with no outbound network under the stated CPU and memory caps, runs the auditor's probes,
   does its own hand checks against the spec, reads the diff, opens the screens at 375 px
   and desktop width, and posts a verdict with evidence.
6. A rejection names the requirement, the reproduction and the revision. Fixes come back as
   **new commits** (never amended) and the whole review is re-run from scratch — nothing is
   carried forward from a rejected round.
7. The coordinator accepts only a revision the reviewer explicitly accepted, writes
   `reports/stage-N.md` (its only commit), and stops.

## 4. What it cost

| Stage | Dispatch → acceptance | Review rounds | Harness (shipped checks) | Auditor probes | Reviewer hand checks |
|---|---|---|---|---|---|
| 1 | 43 min | 1 | 120/120 | 299 | 136 |
| 2 | 6 h 44 min wall clock (≈ 1 h 15 min of seat time; the rest was two provider stalls, see §6) | 2 — rejected, fixed, accepted | 120 + 25 | 407 HTTP + 90 browser | 147 browser + hand checks |
| 3 | 41 min | 1 | 120 + 25 + 7 | 799 HTTP + 103 browser | 67 |
| 4 | 1 h 43 min wall clock (about 30 min of it a usage-window stall, see §6) | 1 | 120 + 25 + 7 + 6 | 1002 HTTP + 116 browser | 58 optimisation and limit checks, plus concurrency and replay checks |

Model spend as Band Desktop estimates it at list prices (not a bill): the development run
that preceded this one cost **$28.36** (four fable seats, stage 1 only, abandoned at a usage
limit). The judged room's running total was **$101** after stage 2, **$139** after stage 3
and **$176** after stage 4, so stages 1 and 2 together cost about $101 and stages 3 and 4
about $38 and $37 each. Band's per-room badge lags, so there is no separate reading for
stage 1. Seats were asked to report usage and none could; the badge is the measurement.

## 5. What review caught

- **Stage 2, round 1 — rejected.** Requirement: a late response to search A must not
  overwrite the results of a later search B. Defect: while a search was in flight the button
  was disabled and Enter was ignored, so B could not start until A returned; the scenario was
  unreachable through the UI and a lost response blocked the form. The reviewer reproduced it
  in Playwright at both viewports by holding A's response. It also showed the implementer's
  own out-of-order test could not observe the bug (Playwright's actionability wait queued B
  behind A) and that the shipped sample suite had no out-of-order test at all. Fix: searches
  are numbered, stale responses dropped, the control stays live during loading. The
  implementer rewrote its test to hold A while B completes and showed it failing on the old
  image before passing on the new one.
- **Stage 2, round 1 — two auditor probe faults**, found by the same review: one probe
  expected a slot that an earlier probe step had just booked; one clicked a disabled cell and
  timed out instead of asserting that nothing opened. The auditor repaired both in a new
  commit; the reviewer re-ran the full suite rather than trusting the diff.
- **Stage 1 — coordinator, pre-review.** The probe run left an untracked `__pycache__` that
  would have failed the reviewer's clean-tree check; fixed by the auditor before routing.
- **Stage 3 — coordinator, pre-dispatch.** The spec named a "restaurant revision" without
  saying where it is read. The coordinator fixed one interpretation for all seats before
  anyone built anything, so implementer and auditor could not diverge.
- **Stage 4 — reviewer, no rejection.** The reviewer did not trust the auditor's suite on
  the optimisation order: the auditor had validated its probes only against its own stub, so
  the reviewer hand-built three optimisation cases (including a two-mover tie decided by the
  rank vector) and reproduced six of the auditor's twelve directly against the service. All
  matched the hand-computed plans. It also corrected one auditor checklist note that had
  marked a constructible tie case as unconstructible.
- Every stage: the reviewer recorded non-blocking observations (seeded-overlap handling,
  bcrypt reset cost, a literal status code) which the next stage's seats inherited.

## 6. Failure modes and recovery

**Provider stalls.** A seat whose turn dies on a rate limit or a dropped connection does
not resume by itself; the rest of the band waits on it. In the judged run this happened to
the reviewer five times, four during stage 2 (overnight) and once during stage 4:

| Stage | Time (local) | Cause | Recovery |
|---|---|---|---|
| 2 | 21:28 → 00:55 | Claude usage-window limit reached mid-review | after the window reset, one human message: `@reviewer continue` |
| 2 | 01:07 → 01:35 | `API Error: Connection dropped (ECONNRESET)` | one human message: `@reviewer continue` |
| 2 | ~02:03 | the same error still held the process | Band Desktop → reviewer → Runtime → *Reset runtime* (restarts the process, keeps the room binding); no message sent |
| 2 | ~02:30 | second `ECONNRESET` | *Reset runtime* again; no message sent |
| 4 | 17:09 → ~18:22 | usage-window limit; all three working seats errored, but implementer and auditor had already handed off | after the window reset, one human message to the reviewer only |

These are the only human inputs after the four dispatches. None carried content about the
work; each is a wake-up or a process restart, and each is visible in `room.json`. Work
completed before a stall was never lost: the reviewer's harness, container and probe results
from before a stall stand in its accepted verdict. The mitigation we adopted: dispatch a
stage only at the start of a fresh usage window, one stage per window.

**Delivery hiccup.** At the end of stage 2 the reviewer's verdict did not reach the
coordinator; only a follow-up hash correction did. The coordinator refused to accept on a
correction alone and demanded the complete verdict as one self-contained message. The
reviewer resent it. This is the mandate working as written: accept a revision, not a seat's
description of it.

**What the design does not catch.** Hidden grading checks beyond the shipped samples; the
factory's defence is the auditor's requirement-derived suite and the reviewer's hand checks,
both of which found real defects the samples did not. A seat that confidently misreads a
requirement the same way the implementer did would pass both; the coordinator's
pre-dispatch interpretation notes reduce but do not remove that risk.

## 7. What we tried and dropped

- **Development run, Friday.** Same mandates, four fable seats, stage 1 only. Stage 1 built
  and passed the shipped checks (120/120) but the review never finished: the shared account
  hit its usage window with six seats running (ours plus a teammate's). Lesson: usage is the
  binding constraint, not model quality; schedule one stage per window.
- **Editing a live agent's configuration.** Band Desktop's edit wizard would not save; the
  Runtime tab's *Save settings* does, and applies on the seat's next wake. Mandate `Model:`
  lines were updated and committed at the same time.
- **A fifth "repairer" seat** was considered and rejected: separating repair from
  implementation would have left fixes unowned by the seat that understood the code and
  added a handoff without adding independence.

## 8. Standing it up elsewhere

1. Create the four seats as in §2, pointing each at its file in `mandates/`.
2. Create a result repository; commit `mandates/` first.
3. Paste a dispatch to `@coordinator` with: the absolute paths, the stage number, where the
   requirements live, the exact verification commands, and the done criteria. The dispatch
   is the only place the problem is named.
4. Do not type in the room until the coordinator's final report. If a seat shows a provider
   error and goes quiet, wait for the limit to clear, then send that seat the single word
   `continue`, or reset its runtime.
5. After the last stage, download the room (**Open in Band → Download full session**) as
   `room.json`, write `README.md`, run `python -m harness check`, push.
