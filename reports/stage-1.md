# Stage 1 report — tablekeeper

## Outcome

**Accepted.** Revision `797afdd1cd56659a1d46908e9aca1267389aadec` (branch `main`), accepted by the reviewer after one review round.

| Item | Value |
|---|---|
| Track / stage | tablekeeper / 1 |
| Stage folder | `stage-1/` (Go 1.25 service, scratch image, `Dockerfile`, `RUN.md`, `probes/`, `tests/smoke.py`) |
| Accepted revision | `797afdd1cd56659a1d46908e9aca1267389aadec` |
| Implementation commits | `6414b04` service, `a8177c4` smoke suite (author implementer) |
| Probe commits | `9642e70` checklist + 299-check suite, `797afdd` `.gitignore` (author auditor) |
| Review rounds | 1 (accepted first time) |
| Wall-clock start (dispatch received) | 2026-10-04T00:14:29Z |
| Wall-clock end (reviewer acceptance verified) | 2026-10-04T00:57:56Z |
| Elapsed | 43 minutes |
| Usage / cost figures | None reported by any seat |

## Timeline

| UTC | Event |
|---|---|
| 00:14 | Dispatch received. Implementer, auditor and reviewer added to the room and verified as members. |
| 00:19 | Build handoff sent to implementer and probe handoff sent to auditor in parallel, each with the full requirements text. |
| 00:40 | Auditor handed off `9642e70`: `probes/CHECKLIST.md` (151 items), `probes/run_probes.py` (299 checks, 14 groups, stdlib only), `probes/README.md`. |
| 00:41 | Coordinator asked the auditor to ignore the `__pycache__` left by running the suite; auditor committed `797afdd`. |
| 00:45 | Implementer handed off: harness run `s1-impl-3` 120/120 "claimed stage: 1", own smoke suite 383/383, clean start on an internal network. |
| 00:47 | Review handoff for `797afdd` sent to reviewer with requirements, both seats' evidence, harness and clean-container commands, probe location and run command. |
| 00:57 | Reviewer accepted `797afdd`. |

## What the reviewer verified (round 1)

- **Harness** `s1-review-2`: stage 1 pass, 120 collected / 120 passed, `claimed stage: 1`, report revision `797afdd`. Stage-2 overshoot line failed as expected.
- **Clean container**: `docker build` from `stage-1/`; `docker run --rm --network none --cpus 2 --memory 2g -e PORT=8080 -p 8080:8080` stays up and logs listening within 1 s; on an `--internal` network a curl sidecar got `GET /health` 200 `{"status":"ok"}` about 2 s after start; outbound from that network fails, so nothing is fetched at run time.
- **Auditor probes**: `python3 run_probes.py --base-url http://127.0.0.1:18090` → 299 passed, 0 failed.
- **Hand checks** (136 of the reviewer's own, zero 5xx): replay after batch move and after cancel; export taken during 50 concurrent writes is internally consistent and round-trips with every replay intact; reset invalidates tokens and keys; idempotency-key boundaries (255 ok, 256 → 422, whitespace-only → 400); non-object bodies → 400 on every JSON endpoint; `PATCH {}` and no-op moves leave bookings unchanged; cutoff measured against the current start after a PATCH; seeded cancelled bookings do not block, seeded confirmed do; New York fall-back 2026-11-01 slots appear once with the pre-transition offset and `ends_at` carries the post-transition offset; spring-forward gaps absent and `invalid_local_time` on booking, PATCH and moves; case-insensitive `email_taken`; reset with 100 bcrypt users in 3.2 s.
- **Code vs requirements**: single mutex around every check-and-write; idempotency record written in the same critical section and only on success; key scoped by user, method and path; canonical JSON body comparison; bcrypt over SHA-256 prehash; tz data embedded via `time/tzdata`; `go vet` and `go build` clean.
- **Maintainability**: one file per concern, named constants for every stated limit, no dead code, no secrets, no symlinks, no nested `.git`, `RUN.md` builds and starts with no manual steps.

## What review caught

No rejection was issued. Items raised during the stage:

- **Coordinator (pre-review)**: running the probe suite left an untracked `stage-1/probes/__pycache__/` that would have made the reviewer's clean-tree check fail. Fixed by the auditor in `797afdd` (new commit, probe content unchanged).
- **Reviewer, non-blocking observations** recorded for the next stage: reset and import accept overlapping confirmed seeded bookings without checking them against each other; reset costs about 32 ms per fixture user (bcrypt cost 10), so a fixture of roughly 300+ users would approach the 10 s test-control timeout; `fixture.go` uses the literal `500` once where `http.StatusInternalServerError` is used elsewhere.
- **Auditor, not automated**: cancel exactly at the cutoff instant; availability for an unknown restaurant (reviewer confirmed 404 by hand); `PATCH {}` (reviewer confirmed 200 unchanged by hand); password hashing and no-outbound-network (reviewer confirmed in code and Dockerfile).

## Interpretation decisions carried into the accepted revision

- Error precedence in `POST /reservations` and `POST /reservation-moves`: parse (400) → auth (401) → idempotency header (400 / 422) → replay or reuse → field validation → restaurant 404 → table 404 → `invalid_local_time` → `outside_opening_hours` → `not_on_slot_grid` → ends after close → `party_exceeds_capacity` → occupancy 409.
- Wrong JSON type for `restaurant_id`, `table_id`, `email`, `password`, `display_name` → 400 `malformed_request`; any wrong type or format for `party_size` and `starts_at_local` → 422 `validation_failed`.
- `display_name` optional on signup; email uniqueness case-insensitive.
- Unknown route 404, wrong method 405, oversized body 413, all with the error envelope.
- PATCH success is 200; timestamps always carry a numeric offset, never `Z`.

## Seats

| Seat | Handle | Commits |
|---|---|---|
| coordinator | `@maneeshreddy28/coordinator` | this report only |
| implementer | `@maneeshreddy28/implementer` | `6414b04`, `a8177c4` |
| auditor | `@maneeshreddy28/auditor` | `9642e70`, `797afdd` |
| reviewer | `@maneeshreddy28/reviewer` | none (verdict only) |
