# Stage 4 report — tablekeeper

## Outcome

**Accepted.** Revision `ed395036da04bada03503437cef84cc5e419a8b5` (branch `main`), accepted by the reviewer in review round 1. This is the final stage of the run.

| Item | Value |
|---|---|
| Track / stage | tablekeeper / 4 (final) |
| Stage folder | `stage-4/` (Go service, scratch image, embedded browser product, `Dockerfile`, `RUN.md`, `probes/`, `tests/`) |
| Built on | accepted stage-3 revision `2586c6b`, copied forward unchanged in `937fa90`; `stage-1/`, `stage-2/` and `stage-3/` untouched since acceptance |
| Accepted revision | `ed395036da04bada03503437cef84cc5e419a8b5` (stage-4 sources identical to `b174129`; `ed39503` adds only probes) |
| Implementation commits (author implementer) | `937fa90` copy, `310491f` closure replans (preview/apply), series amendments, restaurant revision from 0; `d9ac0a4` HTTP tests; `b174129` RUN.md and UI checks |
| Probe commit (author auditor) | `ed39503` |
| Review rounds | 1 (accepted first time) |
| Wall-clock start (dispatch received) | 2026-10-04T21:48:38Z |
| Wall-clock end (acceptance verified) | 2026-10-04T23:32:03Z |
| Elapsed | 1 h 43 min |
| Usage / cost figures | None reported by any seat. The human moved the reviewer and the coordinator's own mandate to other models during this stage after a weekly limit on the previously assigned model; this runtime executed the stage under the newly assigned model. |

## Timeline

| UTC | Event |
|---|---|
| 21:48 | Dispatch received. All four seats already in the room. |
| 21:52 | Build handoff to implementer (copy stage-3 forward first) and probe handoff to auditor, each with all four requirements documents verbatim. Restaurant-revision start changed from 1 (stage-3 decision) to 0 per stage-4.md. |
| 22:07 | Implementer handed off `b174129`: harness `s4-impl-2` all four suites passing, "claimed stage: 4"; own smoke 1152/1152 including nine hand-built optimisation cases; own Playwright check 196/196. |
| 22:08 | Auditor handed off `ed39503`: checklist Part E/stage-4 groups; HTTP runner 1002 checks (203 new); browser runner 116 checks (13 new); genuine stage-3 export fixture; twelve hand-computed optimisation cases (OPT-A through OPT-L, LIM-1..4); validated against a conforming stub and ten single-rule mutants. |
| 22:09 | Review round 1 handoff sent for `ed39503`. |
| 23:32 | Reviewer **accepted** `ed395036da04bada03503437cef84cc5e419a8b5`. |

## What review caught

No rejection was issued. The reviewer independently constructed and hand-verified three of its own optimisation cases before trusting the auditor's suite, and reproduced six of the auditor's twelve OPT cases directly against the implementation (the auditor had validated its probes only against its own stub, not against this implementation). All matched the hand-computed expected plans. Items raised:

- **Reviewer, non-blocking**: after a confirmation refreshes to show a moved booking's new table, the booking form above it still shows the diner's original selection, and the selected cell renders struck through; both are consistent with the form reflecting the original request. Series-amend and replan check resource existence and manager permission before resolving the idempotency key, as in stage 3, with no observable effect.
- **Reviewer to auditor**: the auditor's CHECKLIST item N4-1 said a contested two-mover rank-vector tie case could not be constructed because every mover sits on the single closed table. The reviewer showed this is constructible by displacing a booking on a different table (its own case X1: two closed-table bookings competing for two open tables, with equal move counts and equal total unused seats, decided only by the reference-order rank vector). Flagged as a coverage gap for the auditor's notes, not a defect in any committed probe.

## What the reviewer verified

- **Harness** `s4-review-1`: stage 1 pass 120/120, stage 2 pass 25/25, stage 3 pass 7/7, stage 4 pass 6/6, "claimed stage: 4", report revision `ed39503`.
- **Clean container**: builds; runs under `--network none --cpus 2 --memory 2g` and logs listening; on an `--internal` network a sidecar gets 200 for `/health`, the four screen routes, the three static assets and `/restaurants`; outbound fails.
- **Auditor probes at `ed39503`**: HTTP 1002 passed, 0 failed; browser 116 passed, 0 failed.
- **Optimisation, by hand** (58 of the reviewer's own checks): three self-constructed cases, including a genuine two-mover rank-vector tie (case X1) and a single-versus-pair tie resolved by each booking's own accepted terms rather than current capacities (cases X2, X2b); six of the auditor's twelve cases reproduced directly against the implementation (fewest-moves-wins, pair selection, capacity from accepted terms, undeclared-pair rejection giving `no_feasible_plan`, and an earlier applied closure blocking a later plan); planning limits at the bound (exact optimum) and one past it (`422 planning_limit`, revision unchanged).
- **Concurrency**: 15 concurrent applies of one plan with distinct keys gave one 201 and fourteen 409s with each booking moved exactly once and the restaurant revision rising by exactly 1; 15 with one key gave one 201 and fourteen identical 200s; two competing plans gave one 201 and nine 409; ten concurrent series amendments on one revision gave one 201 and nine `stale_revision`; a closure at another restaurant left this restaurant's revision and plan unaffected.
- **Replays**: a preview replay after a new booking returned the original plan, which then correctly failed apply with `stale_plan`; an apply replay after a later cancel returned the original response unchanged; a series-amend replay after further edits returned the original.
- **Series and closures**: an amendment moved two occurrences and skipped an exception and a cancelled occurrence, raising the series and restaurant revisions once each with no new exceptions; a plan spanning two occurrences raised the series revision once while preserving exception flags; a moved occurrence stayed eligible for later amendment; a closed table and its pair dropped from availability and explain and rejected every write path during the interval, then became available again outside it; a closure that stranded a booking gave `no_feasible_plan`.
- **Restaurant revision**: 0 after reset; seeds and previews never count; a no-move plan application counts once; export and import preserve an applied closure and a pending plan.
- **Browser** (21 checks at 375x740 and 1280x900, zero foreign requests): a diner's booking was moved by an applied plan; the unchanged resubmit kept the same reference and showed the new table because the client re-reads the booking after replay; the grid matched availability around the closure window; lookup showed the new table; the stage-2 visual system was unchanged.
- **Code read** (`310491f`, `d9ac0a4`, `b174129` against stage-4.md): the planner is a full depth-first search with no heuristic fallback and correct pruning so the first optimum found has the smallest rank vector; feasibility uses each booking's own accepted terms; apply checks not-found, then `plan_already_applied`, then `stale_plan`, writing only after all checks pass; closures are enforced inside the single occupancy check everywhere; series amendment checks input validity before `stale_revision`, then per-occurrence cutoff, policy and occupancy in index order, excluding exception and cancelled occurrences; all four coordinator decisions were followed. `go vet` and build clean.
- **Maintainability**: named constants for the planning limits and the revision start value; the planner is separated from the HTTP handlers; no dead code, no secrets, nothing fetched at run time; `RUN.md` builds and starts with no manual steps.

## Coordinator decisions recorded across this stage

1. **Restaurant revision start**: stage-4.md overrides the stage-3 interpretation. It starts at 0 after reset (not 1), rises once per successful create, real amendment, cancel, publication, adoption, batch with a real change, series amendment with a real change, and plan application; previews, no-ops, failures and replays never change it. Stage-1/2 imports start at 0; stage-3/4 imports preserve the exported value.
2. **Considered bookings** for a replan are every confirmed booking at the restaurant overlapping the closure interval, on any table.
3. **Option ranks and objective**: singles in fixture order then declared pairs in combinable order, from 0; lexicographic objective over considered bookings in ascending reference order: table-set changes, then total unused seats under each booking's own accepted terms, then the rank vector.
4. **Planning limits**: `422 planning_limit` beyond 6 tables, 4 declared pairs or 6 considered bookings; the exact optimum is required within those limits.

All four were followed by both seats and independently verified by the reviewer.

## Seats

| Seat | Handle | Model this stage | Commits |
|---|---|---|---|
| coordinator | `@maneeshreddy28/coordinator` | claude-sonnet-5 (claude-fable-5-1 for earlier stages) | this report only |
| implementer | `@maneeshreddy28/implementer` | claude-opus-5-5 | `937fa90`, `310491f`, `d9ac0a4`, `b174129` |
| auditor | `@maneeshreddy28/auditor` | claude-opus-5-5 | `ed39503` |
| reviewer | `@maneeshreddy28/reviewer` | claude-opus-5-5 (claude-fable-5-1 for stages 1-3) | none (verdict only) |
