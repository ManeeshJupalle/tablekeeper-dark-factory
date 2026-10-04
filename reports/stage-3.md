# Stage 3 report — tablekeeper

## Outcome

**Accepted.** Revision `2586c6b2fa52d223ffb16170f228fb98f6e01a87` (branch `main`), accepted by the reviewer in review round 1.

| Item | Value |
|---|---|
| Track / stage | tablekeeper / 3 |
| Stage folder | `stage-3/` (Go service, scratch image, embedded browser product, `Dockerfile`, `RUN.md`, `probes/`, `tests/`) |
| Built on | accepted stage-2 revision `cbafad2`, copied forward unchanged in `aa3de07`; `stage-1/` and `stage-2/` untouched since acceptance |
| Accepted revision | `2586c6b2fa52d223ffb16170f228fb98f6e01a87` (stage-3 sources identical to `5035b4c`; `2586c6b` adds only probes) |
| Implementation commits (author implementer) | `aa3de07` copy, `081de22` policies, terms, revisions, history, series, explain; `4a7bcf2` HTTP tests; `3584600` UI seat labels from policy plus UI tests; `5035b4c` RUN.md and .gitignore |
| Probe commit (author auditor) | `2586c6b` |
| Review rounds | 1 (accepted first time) |
| Wall-clock start (dispatch received) | 2026-10-04T11:07:43Z |
| Wall-clock end (acceptance verified) | 2026-10-04T11:48:20Z |
| Elapsed | 41 minutes |
| Usage / cost figures | None reported by any seat |

## Timeline

| UTC | Event |
|---|---|
| 11:07 | Dispatch received. All four seats already in the room. |
| 11:10 | Build handoff to implementer (copy stage-2 forward first) and probe handoff to auditor, each with stage-1.md, stage-2.md and stage-3.md verbatim. |
| 11:11 | Implementer committed the unchanged copy `aa3de07`; coordinator verified the tree diff against `cbafad2:stage-2` is empty. |
| 11:33 | Implementer handed off `5035b4c`: harness `s3-impl-2` stage 1 120/120, stage 2 25/25, stage 3 7/7, "claimed stage: 3"; own smoke 863/863 with the accepted stage-1 and stage-2 images as upgrade sources; own Playwright check 178/178. |
| 11:37 | Auditor handed off `2586c6b`: checklist Part C items 238-443; HTTP runner 799 checks (392 new); browser runner 103 checks (13 new); genuine stage-2 export fixture; validated against a conforming stub and six single-rule mutants. |
| 11:38 | Review round 1 handoff sent for `2586c6b`. |
| 11:48 | Reviewer **accepted** `2586c6b`. |

## Coordinator decision recorded for this stage

stage-3.md names a "restaurant revision" (adoption increments it once; a batch increments it once) without saying where it is read. Before dispatch the coordinator fixed the interpretation for all seats: an integer field `revision` on `GET /restaurants/{id}`, starting at 1 at reset or import, incremented exactly once per successful state-changing operation on that restaurant (policy publication, booking create, real amendment, cancel, series adoption, one per successful batch), never by failures or replays. The implementer added two consistent refinements: a batch whose items are all no-ops changes no revision, and the restaurant revision is preserved by stage-3 export and import. The reviewer verified all of it by hand.

## What review caught

No rejection was issued. Items raised during the stage:

- **Reviewer, non-blocking**: the policy endpoint checks restaurant existence and manager permission before resolving the idempotency key (no observable effect since managers come only from fixtures); seeded overlapping reservations remain accepted on reset and import, unchanged since stage 1.
- **Auditor, not automated**: the exact cutoff instant; restaurant revision after a stage-3 round trip (reviewer confirmed it is preserved); whether an all-no-op batch changes the restaurant revision (reviewer confirmed it does not); order of `stale_revision` versus `reservation_cancelled` (reviewer confirmed `expected_revision` is checked first); a huge positive `expected_revision` (reviewer confirmed 2^63−1 gives 409 `stale_revision`, 2^63 and 1.0 give 422); wrong JSON types inside a policy body (implementation gives 422, which the auditor accepts); contrast and visual quality (reviewer's browser judgement).

## What the reviewer verified

- **Harness** `s3-review-1`: stage 1 pass 120/120, stage 2 pass 25/25, stage 3 pass 7/7, "claimed stage: 3", report revision `2586c6b`. Stage-4 overshoot fail ignored.
- **Clean container**: builds; runs under `--network none --cpus 2 --memory 2g` and logs listening; on an `--internal` network a sidecar gets 200 for `/health`, the four screen routes, the three static assets and `/restaurants`; outbound to example.com and 1.1.1.1 fails; no external URLs in HTML, CSS or JS.
- **Auditor probes at `2586c6b`**: HTTP 799 passed, 0 failed; browser 103 passed, 0 failed.
- **Required hand checks** (67 of the reviewer's own): genuine stage-2 export imported (revision 1, policy-0 terms, created entries at `created_at`, verbatim receipt replays, adoption of an imported pair, re-export identical after re-import); 20 concurrent guarded PATCHes give one 200 and 19 `stale_revision`; 20 unguarded amendments give revision 21 with 21 history entries; 12 concurrent adoptions give one 201 and 11 `already_in_series` with one restaurant-revision increment; 10 concurrent publications give versions 1..10; replays of booking, series and batch after later amendments return the originals and change nothing; history after pair creation, pair-to-single change, no-op, failed PATCH, publication, batch and cancel; policy selection with same-date ties, out-of-order publication, policy 0 before any policy, a far-past closing policy, and existing bookings blocking with their own duration under a shorter policy; explain shape and rejection of every non-`true` value; series across the Berlin fall-back, a spring-forward gap rejecting the whole adoption with nothing partial, and first failing index deciding the code; restaurant revision deltas; all-no-op batch; boundary `expected_revision`; stage-3 import rejecting a policy-version gap or unknown accepted version with the destination unchanged; one key string used on two paths as two first uses; a stricter policy making an existing pair booking exceed capacity (reversed-pair no-op keeps old terms, real change 422, availability false).
- **Browser** (33 checks at 375x740 and 1280x900, zero foreign requests): a published 60-minute, 120-minute-duration, Thursday-only policy drives the grid (exactly four slots), every cell matches the API, seat labels come from the policy capacities, the day before is closed and a Friday before the policy uses fixture rules; a booking adopts version-1 terms with a 120-minute end; lost pair response recovery with the same key and body; lookup, cancel and history from the browser; stage-2 visual system unchanged.
- **Code read** (`081de22`, `3584600` against stage-3.md): single mutex for every check-and-write; history, revisions, exception flags and counters written only after all checks pass; policy selection by effective date with ties to version; overlaps by each booking's stored end; cutoff from accepted terms everywhere; order-insensitive no-op detection; `stale_revision` before cancelled, cutoff and validation; import preserves ids, references, timestamps and histories. `go vet` and build clean.
- **Maintainability**: named constants for every stated limit; policy, history and series in their own files documented in RUN.md; no TODOs, no secrets; fixtures hold only throwaway probe credentials and are not in the image; RUN.md builds and starts with no manual steps.

## Interpretation decisions carried into the accepted revision

- Policy body: any invalid content, including wrong JSON types, is 422 `validation_failed`; echoed fields are the six policy fields plus `policy_version`.
- `GET /restaurants/{id}` keeps the fixture configuration and adds `combinable`, `manager_user_ids` and `revision`.
- PATCH order: 404, `expected_revision` (422 or 409 `stale_revision`), cancelled, accepted cutoff, field parsing, no-op check, full validation under the resulting date's policy, occupancy. A no-op keeps its old terms even when a newer policy governs the date.
- Series: non-string `anchor_reference` is 400; missing is 422.
- History `at` is UTC; the created entry's `at` equals `created_at`. Seeded (including cancelled) and imported stage-1/2 reservations get revision 1, policy-0 terms and one created entry.
- Stage-3 import requires policy versions 1..n in order and restores terms by `policy_version`.
- UI: unchanged except grid seat labels use the capacities of the policy in force for the searched date.

## Seats

| Seat | Handle | Model this stage | Commits |
|---|---|---|---|
| coordinator | `@maneeshreddy28/coordinator` | claude-fable-5-1 | this report only |
| implementer | `@maneeshreddy28/implementer` | claude-opus-5-5 | `aa3de07`, `081de22`, `4a7bcf2`, `3584600`, `5035b4c` |
| auditor | `@maneeshreddy28/auditor` | claude-opus-5-5 | `2586c6b` |
| reviewer | `@maneeshreddy28/reviewer` | claude-fable-5-1 | none (verdict only) |
