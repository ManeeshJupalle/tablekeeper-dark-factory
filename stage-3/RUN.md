# Tablekeeper — stage 3

A Go HTTP service for restaurant reservations with a bundled browser product
(search and availability grid, booking with safe retries, signup, login and
reservation lookup), combined-table bookings, manager-published dated booking
policies whose terms each booking keeps, per-reservation history and decisions,
availability explanations and recurring series. All state lives in memory; the
container needs no volumes, no external services and no network at run time.
Every screen asset (HTML, CSS, JavaScript, icon) is compiled into the binary.

## Build and run

From this folder:

```sh
docker build -t tk-stage3 .
docker run --rm -e PORT=8080 -p 8080:8080 tk-stage3
```

The service listens on `0.0.0.0:$PORT` (default 8080) and answers
`GET /health` with `200 {"status":"ok"}` as soon as it starts. Open
`http://localhost:8080/` for the search screen; `/signup`, `/login` and
`/lookup` are the other screens.

To mirror the grading limits:

```sh
docker run --rm --network none --cpus 2 --memory 2g -e PORT=8080 -p 8080:8080 tk-stage3
```

## Layout

| File | Responsibility |
|---|---|
| `main.go` | Process entry point, HTTP server settings |
| `router.go` | Route table (API and screens), method dispatch, panic recovery |
| `web.go` | Embedded screens and static assets, restaurant options injected into the search screen |
| `web/*.html`, `web/static/*` | The four screens, one stylesheet, one script, favicon |
| `api.go` | Error envelope, JSON body parsing, shared field helpers |
| `timeutil.go` | Bare local times and dates, DST-aware resolution, RFC 3339 output |
| `model.go` | Domain types (terms, policies, restaurants, reservations), in-memory `State`, single-lock `Store` |
| `auth.go` | Signup, login, bcrypt password hashing, bearer authentication |
| `restaurants.go` | Restaurant listing/detail, availability under the date's policy, `explain=true` |
| `policy.go` | Policy validation, manager publication, public policy list, terms views |
| `reservations.go` | Table-set and policy validation, create (idempotent), list, read, cancel, PATCH with revisions |
| `history.go` | History entries for create/change/cancel, owner-only history and decision endpoints |
| `moves.go` | `POST /reservation-moves`: atomic multi-booking changes under PATCH semantics |
| `series.go` | `POST /series` and `GET /series/{id}`: recurring occurrences, exceptions, series revision |
| `fixture.go` | Fixture/import decoding and validation into a `State` |
| `testctl.go` | `/_test/reset`, `/_test/export`, `/_test/import` (accepts stage-1 and stage-2 exports) |
| `tests/smoke.py`, `tests/stage3_smoke.py` | HTTP checks for every stage-1, stage-2 and stage-3 rule (Python 3, stdlib only) |
| `tests/ui_check.py` | Browser checks for every screen at 375 px and desktop (Playwright) |

## Running the checks

With the container up on port 8080 (optionally the accepted stage-1 and stage-2
images on other ports, whose exports are imported to check the upgrade paths):

```sh
python3 tests/smoke.py http://127.0.0.1:8080 [stage-1 URL] [stage-2 URL]
python3 tests/ui_check.py http://127.0.0.1:8080         # needs `pip install playwright` and `playwright install chromium`
```
