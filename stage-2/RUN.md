# Tablekeeper — stage 2

A Go HTTP service for restaurant reservations with a bundled browser product:
search and availability grid, booking with safe retries, signup, login and
reservation lookup, plus combined-table bookings. All state lives in memory; the
container needs no volumes, no external services and no network at run time.
Every screen asset (HTML, CSS, JavaScript, icon) is compiled into the binary.

## Build and run

From this folder:

```sh
docker build -t tk-stage2 .
docker run --rm -e PORT=8080 -p 8080:8080 tk-stage2
```

The service listens on `0.0.0.0:$PORT` (default 8080) and answers
`GET /health` with `200 {"status":"ok"}` as soon as it starts. Open
`http://localhost:8080/` for the search screen; `/signup`, `/login` and
`/lookup` are the other screens.

To mirror the grading limits:

```sh
docker run --rm --network none --cpus 2 --memory 2g -e PORT=8080 -p 8080:8080 tk-stage2
```

## Layout

| File | Responsibility |
|---|---|
| `main.go` | Process entry point, HTTP server settings |
| `router.go` | Route table (API and screens), method dispatch, panic recovery |
| `web.go` | Embedded screens and static assets, restaurant options injected into the search screen |
| `web/*.html`, `web/static/*` | The four screens, one stylesheet, one script, favicon |
| `api.go` | Error envelope, JSON body parsing, shared field helpers |
| `timeutil.go` | Bare local times, DST-aware resolution, RFC 3339 output |
| `model.go` | Domain types (tables, combinable pairs, table sets), in-memory `State`, single-lock `Store` |
| `auth.go` | Signup, login, bcrypt password hashing, bearer authentication |
| `restaurants.go` | Restaurant listing/detail, availability slots with `available_options` |
| `reservations.go` | Table-set validation, create (idempotent), list, read, cancel, PATCH |
| `moves.go` | `POST /reservation-moves`: atomic multi-booking changes |
| `fixture.go` | Fixture/import decoding and validation into a `State` |
| `testctl.go` | `/_test/reset`, `/_test/export`, `/_test/import` (accepts stage-1 exports) |
| `tests/smoke.py` | HTTP checks for every stage-1 and stage-2 rule (Python 3, stdlib only) |
| `tests/ui_check.py` | Browser checks for every screen at 375 px and desktop (Playwright) |

## Running the checks

With the container up on port 8080:

```sh
python3 tests/smoke.py http://127.0.0.1:8080            # add a stage-1 service URL as a second argument to check the upgrade import
python3 tests/ui_check.py http://127.0.0.1:8080         # needs `pip install playwright` and `playwright install chromium`
```
