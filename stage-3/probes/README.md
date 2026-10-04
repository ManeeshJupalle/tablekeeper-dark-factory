# Tablekeeper stage 2 - auditor probes

Black-box probes derived from the stage-1 and stage-2 requirements alone. They never read
the implementation. `CHECKLIST.md` lists every contract item (Part A: stage 1, still in force;
Part B: stage 2) with the probe id that covers it.

Two runners:

| Runner | What it covers | Dependencies |
|---|---|---|
| `run_probes.py` | HTTP contract: all stage-1 groups plus stage-2 combined tables, idempotency, moves, concurrency and the stage-1-to-stage-2 upgrade | Python 3.11+, standard library only |
| `run_browser_probes.py` | The four screens, data-testid contract, out-of-order / 409 / lost-response recovery, upgrade in the browser, viewport and focus rules | Python 3.11+, Playwright for Python with Chromium |

## Install (browser runner only)

```sh
pip install playwright && python -m playwright install chromium
```

The HTTP runner needs nothing installed.

## Run

Start the stage-2 service, then from this directory:

```sh
python3 run_probes.py --base-url http://127.0.0.1:8080
python3 run_browser_probes.py --base-url http://127.0.0.1:8080
```

`--base-url` defaults to `http://127.0.0.1:8080`; `TABLEKEEPER_BASE_URL` is used when the flag
is omitted. `--only GROUP,GROUP` runs a subset of groups (HTTP: `CFX,CAV,...`; browser:
`UI-GRID,UI-LOST,...`). Run the two runners one after the other, not concurrently: both reset the
service state.

Each check prints one line, `PASS <id> <description>` or
`FAIL <id> <description> -- <reason>`, followed by `# <n> passed, <m> failed`. Exit status is
0 only when every check passes.

## What the runs do

Every group starts with `POST /_test/reset` using the probes' own fixtures. Stage-2 groups use
restaurant `r_combo` (tables Alcove, Bay, Corner, Den; combinable Bay+Alcove and Bay+Corner) next
to the unchanged stage-1 restaurants. Far-future dates are computed relative to the run date
(first Thursday at least 21 days ahead). Cutoff probes use `r_zulu` (zone `Etc/UTC`, 5-minute
slots, 60-minute cutoff) with bookings about 48 and 68 minutes ahead of the probe host clock, so
host and container clocks must agree within a few minutes.

`fixtures/stage1_export.json` is a genuine `GET /_test/export` captured from the accepted stage-1
image (revision 797afdd) after seeding `r_combo` without `combinable`, logging in, signing up a
user, booking twice, running a batch, cancelling once and failing one idempotency key.
`fixtures/stage1_export_expected.json` records the tokens, keys, bodies and original responses
from that session. The UPG group imports the export into the stage-2 service and checks that
tokens, logins, references, lookups and replays survive. Both files hold only throwaway test
credentials for the fixture users (`hana@probe.test`, `nova@probe.test`); they are test
artefacts, not secrets.

The browser runner uses headless Chromium at 1280 px, plus 375 px and 1280 px for the viewport
group. Every page aborts and records any request to a host other than the service; the run
fails if one occurs. Recovery scenarios use Playwright route interception: holding an
availability response to reorder searches, letting a booking POST reach the server and then
dropping the response, or dropping the request before it reaches the server.

Timeouts are 5 s per request and action, 10 s for `/_test/reset`, `/_test/import` and
`/_test/export`. Concurrency groups fire 17 to 24 simultaneous requests, within the 50 in-flight
limit.

## Known limits

- The cutoff boundary instant itself cannot be hit deterministically; the probes test about
  12 minutes either side.
- If the probe host clock is within 10 minutes of midnight UTC the `r_zulu` cutoff bookings may
  fall outside its opening hours; rerun a few minutes later.
- The requirements do not name the success status of `PATCH /reservations/{reference}`; the
  probes expect 200, matching cancel in the same section.
- Stage-1 replays after the upgrade import are accepted either identical to the original body
  or identical plus `table_ids == [table_id]`, because the documents require both "identical
  to the original response" and "responses always carry table_ids".
- The loading-state check (UI-25) is a heuristic over several accepted signals, because the
  documents define no testid for it. Contrast and visual quality are not automated.
