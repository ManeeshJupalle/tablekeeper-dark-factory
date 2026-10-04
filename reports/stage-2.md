# Stage 2 report — tablekeeper

## Outcome

**Accepted.** Revision `cbafad21236b140e1245d4ba1bc2911dba16e600` (branch `main`), accepted by the reviewer in review round 2 after one rejection.

| Item | Value |
|---|---|
| Track / stage | tablekeeper / 2 |
| Stage folder | `stage-2/` (Go 1.25 service, scratch image, embedded browser product, `Dockerfile`, `RUN.md`, `probes/`, `tests/`) |
| Built on | accepted stage-1 revision `797afdd`, copied forward unchanged in `f05f5cb`; `stage-1/` untouched since acceptance |
| Accepted revision | `cbafad21236b140e1245d4ba1bc2911dba16e600` |
| Implementation commits (author implementer) | `f05f5cb` copy, `2ebd191` API, `5e89df3` UI, `d08aef1` tests, `9d4cce8` sign-in header, `e63c775` RUN.md, `0704f21` UI refinements, `84d5da4` out-of-order fix, `cbafad2` tests |
| Probe commits (author auditor) | `edd7a1a` stage-2 probes, `68bf379` probe repairs |
| Review rounds | 2 (round 1 rejected `0704f21`; round 2 accepted `cbafad2`) |
| Wall-clock start (dispatch received) | 2026-10-04T01:38:24Z |
| Wall-clock end (acceptance verified) | 2026-10-04T08:22:35Z |
| Elapsed | 6 h 44 min |
| Usage / cost figures | None reported by any seat. The human rebalanced implementer and auditor to `claude-opus-5-5` before this stage (`1378162`) citing stage-1 cost measurement; reviewer and coordinator stayed on `claude-fable-5-1`. |

## Timeline

| UTC | Event |
|---|---|
| 01:38 | Dispatch received. All four seats already in the room. |
| 01:4x | Build handoff to implementer (copy stage-1 forward first) and probe handoff to auditor (HTTP plus Playwright), each with stage-1.md and stage-2.md verbatim. |
| 01:5x | Implementer committed the unchanged copy `f05f5cb`; coordinator verified the tree diff against `797afdd:stage-1` is empty. |
| 02:03 | Auditor handed off `edd7a1a`: checklist extended to 237 items, HTTP runner 407 checks, browser runner 90 checks, captured stage-1 export fixture for the upgrade group. |
| 02:14 | Implementer handed off `0704f21`: harness `s2-impl-3` stage 1 120/120, stage 2 25/25, "claimed stage: 2"; own smoke 503/503; own Playwright check 146/146. |
| 02:17 | Review round 1 handoff sent for `0704f21`. |
| 06:05 | Reviewer **rejected** `0704f21` (one requirement) and reported two probe faults. Coordinator forwarded both to implementer and auditor with full reproduction. |
| 06:08 | Auditor committed probe repairs `68bf379`. |
| 06:13 | Implementer handed off `cbafad2`: harness `s2-impl-4` claimed stage 2; own UI check 164/164; smoke 506/506; auditor probes 407/407 and 90/90. |
| 06:14 | Review round 2 handoff sent for `cbafad2` with probes at `68bf379`. |
| 08:2x | Reviewer **accepted** `cbafad2`. A hash typo in one line of the verdict was corrected by the reviewer in a follow-up; the heading, harness report and correction all name `cbafad21236b140e1245d4ba1bc2911dba16e600`. |

## What review caught

### Round 1 rejection of `0704f21` (implementation)

**Requirement** (stage-2.md, "Competing clients and uncertain outcomes"): if search A starts before search B but finishes after it, the grid, table labels and booking form must describe B; a late response must not restore A.

**Defect**: while a search was in flight the search button was disabled and relabelled "Checking…", and Enter submitted nothing. Search B could not start until A's response arrived, so the out-of-order scenario was unreachable through the UI, and a search whose response never arrived blocked the form. The reviewer reproduced it with Playwright at both viewports by holding the `/availability` response for one restaurant and attempting a second search by click and by Enter. The reviewer also showed that the implementer's own out-of-order test could not observe the scenario (Playwright's actionability wait simply queued B behind A) and that the shipped sample suite has no out-of-order test.

**Fix** (`84d5da4`): the button stays clickable and Enter submits during a search; loading remains visible (spinner, "Checking…", `aria-busy`); every search is numbered and any response not for the latest search is dropped, including post-booking and post-409 refreshes; a new search clears the old form. The implementer rewrote their own check to hold A while B completes and confirmed it failed on the old image before passing on the new one.

### Round 1 probe faults (auditor)

- **UPG-7a**: expected a table free at 19:00 that an earlier probe step had just booked under a reused failed key. Repaired by checking occupancy before that step and additionally asserting the cancelled stage-1 booking's slot is free.
- **UI-8a**: clicked an unavailable cell with a plain click; the implementation renders unavailable cells as disabled buttons, so the click timed out and four later checks never ran. The reviewer judged a disabled cell compliant with "clicking an unavailable cell does nothing" (the shipped sample test clicks with `force=True` and says so). Repaired to click without actionability checks and assert no form opened.
- **UI-12a to UI-12c** raised an exception rather than failing cleanly when B could not start; repaired to report the button state as a FAIL.

### Non-blocking observations recorded by the reviewer

- Reset and import accept mutually overlapping seeded confirmed bookings (unchanged since accepted stage 1).
- Duplicate or reversed-duplicate `combinable` entries produced duplicate options and cells at `0704f21`; the implementer deduplicated them in `84d5da4` and the reviewer confirmed in round 2.
- One unused CSS selector; after a successful booking the booked cell renders as both selected and struck through.
- The lookup screen requires sign-in because the API does, and says so with a login link.

## What the reviewer verified in round 2 (all re-run against `cbafad2`, nothing carried forward)

- **Harness** `s2-review-2`: stage 1 pass 120/120, stage 2 pass 25/25, "claimed stage: 2", report revision `cbafad2`. Stage-3 overshoot fail ignored.
- **Clean container**: builds; runs under `--network none --cpus 2 --memory 2g` and logs listening; on an `--internal` network a sidecar gets 200 for `/health`, the four screen routes and the three static assets the pages reference; outbound to example.com and 1.1.1.1 fails. Health answered in 2 ms on the bridge run.
- **Auditor probes at `68bf379`**: HTTP 407 passed, 0 failed; browser 90 passed, 0 failed.
- **Real-browser re-verification** at 375x740 and 1280x900 (147 checks, zero requests leaving the service origin): held A, started B by click and by Enter for another restaurant and party size, B's grid, labels and combined-table booking form rendered while A was pending and remained after A was released; a never-answered search no longer blocks; loading state visible and distinct; no horizontal scroll on any route; visible labels, focus rings on inputs, buttons and grid cells; `current-user` and logout on every screen; signed-out click shows `auth-error`; closed day shows `no-slots`; lookup, cancel and not-found flows; unknown path 404 JSON, wrong method 405 JSON, no 5xx.
- **Visual judgement**: coherent warm hospitality system (cream and paper surfaces, terracotta primary, serif headings); hierarchy date, time, party, seating; human-readable restaurant and table labels throughout; combined tables shown as "Tables Bay + Window · seats 6" under a "Combined tables" group; available, unavailable, selected, loading, success, refused and uncertain states visually distinct. The reviewer's verdict: it reads as a product, not a harness.
- **Hand checks beyond the suites**: same-restaurant out-of-order with different party sizes; late A after B's combination booking succeeded; a new search while a lost-response retry is pending; triple search with only the middle one held; reversed-duplicate `combinable` deduplication through restaurants, availability, grid and booking normalisation; failed B with A held; 409 refresh dropped when superseded by a newer search; party size changed and changed back mints a new key; lost response on a combination booking recovered with the same key and body; keyboard-only grid operation; 50 concurrent mixed pair and single bookings at one slot across ten users gave 2 winners and 48 refusals with no table in two winners and no 5xx.
- **Code and maintainability**: single mutex around every check-and-write including per-member occupancy; stage-1 export imports and stage-1 receipts replay verbatim; `go vet` and build clean; named constants for stated limits; bcrypt over SHA-256 prehash; everything embedded with `go:embed`; `RUN.md` builds and starts with no manual steps; the committed probe fixture holds only throwaway bcrypt hashes and probe tokens and is not copied into the image, judged acceptable as a private test artefact under stage-1 §10.

## Interpretation decisions carried into the accepted revision

- Table-set check order in POST, PATCH and moves: duplicates (422 `validation_failed`) → more than two (422 `combination_not_allowed`) → unknown table (404) → undeclared pair (422 `combination_not_allowed`) → time rules → summed capacity → occupancy. Empty `table_ids` is 422 `validation_failed`; wrong JSON type is 400 `malformed_request`.
- Response `table_ids` for a pair are normalised to `combinable` order; `table_id` appears only for one-member sets.
- Seeded bookings may use `table_id` or `table_ids` (both or neither is 422); seeded pairs are not checked against `combinable` or each other.
- `GET /restaurants/{id}` and the export include `combinable` (empty when omitted). Stage-1 receipts imported from a stage-1 export replay their original body verbatim.
- Screen routes answer GET only; unknown paths stay 404 JSON. Unavailable cells are disabled buttons. Session is kept in `localStorage` so sign-in survives a stage-1 to stage-2 import between browser requests.

## Seats

| Seat | Handle | Model this stage | Commits |
|---|---|---|---|
| coordinator | `@maneeshreddy28/coordinator` | claude-fable-5-1 | this report only |
| implementer | `@maneeshreddy28/implementer` | claude-opus-5-5 | `f05f5cb` … `cbafad2` (9) |
| auditor | `@maneeshreddy28/auditor` | claude-opus-5-5 | `edd7a1a`, `68bf379` |
| reviewer | `@maneeshreddy28/reviewer` | claude-fable-5-1 | none (verdicts only) |
