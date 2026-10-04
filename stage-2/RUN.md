# Tablekeeper — stage 1

A Go HTTP service for restaurant reservations. All state lives in memory; the
container needs no volumes, no external services and no network at run time.

## Build and run

From this folder:

```sh
docker build -t tk-stage1 .
docker run --rm -e PORT=8080 -p 8080:8080 tk-stage1
```

The service listens on `0.0.0.0:$PORT` (default 8080) and answers
`GET /health` with `200 {"status":"ok"}` as soon as it starts.

To mirror the grading limits:

```sh
docker run --rm --network none --cpus 2 --memory 2g -e PORT=8080 -p 8080:8080 tk-stage1
```

## Layout

| File | Responsibility |
|---|---|
| `main.go` | Process entry point, HTTP server settings |
| `router.go` | Route table, method dispatch, panic recovery |
| `api.go` | Error envelope, JSON body parsing, shared field helpers |
| `timeutil.go` | Bare local times, DST-aware resolution, RFC 3339 output |
| `model.go` | Domain types, in-memory `State`, the single-lock `Store`, id generation |
| `auth.go` | Signup, login, bcrypt password hashing, bearer authentication |
| `restaurants.go` | Restaurant listing/detail and availability slots |
| `reservations.go` | Booking validation, create (idempotent), list, read, cancel, PATCH |
| `moves.go` | `POST /reservation-moves`: atomic multi-booking changes |
| `fixture.go` | Fixture/import decoding and validation into a `State` |
| `testctl.go` | `/_test/reset`, `/_test/export`, `/_test/import` |
| `tests/smoke.py` | End-to-end checks against a running container (Python 3, stdlib only) |

## Running the smoke checks

With the container up on port 8080:

```sh
python3 tests/smoke.py http://127.0.0.1:8080
```
