# Tablekeeper — built by a four-seat dark factory

**Team:** Almanac · **Track:** `tablekeeper` · **Event:** WeAreDevelopers x BAND — AI Dark Factory (hackathon edition)

A restaurant reservation service — search, availability, atomic bookings, combined tables,
dated booking policies, history, recurring agreements, closure re-seating — built by four
Claude Code seats coordinating in one Band Desktop room, with one human message per stage.

## What is in this repository

| Path | Contents |
|---|---|
| `FACTORY.md` | The factory: seats, configuration, workflow, measured cost, what review caught, failure handling, how to stand it up on another problem |
| `mandates/` | One standing instruction per seat (`coordinator.md`, `implementer.md`, `auditor.md`, `reviewer.md`). Generic: nothing in them names this track |
| `room.json` | The Band Desktop room, downloaded unchanged — every handoff, verdict, tool call and commit reference |
| `reports/stage-N.md` | The coordinator's report per stage: accepted revision, timeline, what review caught, interpretation decisions |
| `stage-1/` … `stage-4/` | One complete, independently buildable service per stage. Each later folder is the previous one copied forward unchanged and then extended |

## Stages

| Folder | Adds | Accepted revision | Review rounds |
|---|---|---|---|
| `stage-1/` | JSON API: accounts, availability, idempotent bookings, atomic multi-booking moves, DST-correct local time, state export/import | `797afdd` | 1 |
| `stage-2/` | Browser product (search grid, booking, confirmation, lookup) with out-of-order and lost-response recovery; combined tables | `cbafad2` | 2 (one rejection) |
| `stage-3/` | Manager-published dated policies with per-booking accepted terms, revisions, history, availability explanations, recurring series | `2586c6b` | 1 |
| `stage-4/` | Closure re-seating: read-only plan preview, atomic application, deterministic optimisation; recurring-series amendments | `ed39503` | 1 |

Every folder passes the shipped checks for its own stage and every earlier stage in
isolated mode (`harness run --mode isolated`), including the upgrade paths from each earlier
folder's exported state.

## Run the final service

```sh
docker build -t tablekeeper ./stage-4
docker run --rm --network none --cpus 2 --memory 2g -e PORT=8080 -p 8080:8080 tablekeeper
```

Then open `http://localhost:8080/`. `GET /health` returns `{"status":"ok"}`. Each stage
folder's `RUN.md` has the same for that stage. The service is Go with no runtime
dependencies, all browser assets embedded; nothing is fetched at run time.

## Human inputs in the judged run

Four dispatches (one per stage), three wake-up messages to the reviewer after provider
stalls, two runtime resets of the reviewer after dropped connections. No steering,
hints or reruns. Details and timestamps in `FACTORY.md` §6; all of it is in `room.json`.

## Where to look first

- The stage-2 rejection and fix: `reports/stage-2.md` → "What review caught".
- The auditor's requirement-derived suites: `stage-N/probes/CHECKLIST.md` and the runners
  beside it. These are the checks the shipped samples never asked for.
- The coordinator's interpretation decisions before dispatch: `reports/stage-3.md`.
