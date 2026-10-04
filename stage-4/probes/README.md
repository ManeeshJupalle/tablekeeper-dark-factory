# Tablekeeper stage 4 - auditor probes

Black-box probes derived from the stage-1 to stage-4 requirements alone. They never read
the implementation. `CHECKLIST.md` lists every contract item (Part A: stage 1; Part B: stage 2; Part C: stage 3;
Part D: stage 4; all in force) with the probe id that covers it.

Two runners:

| Runner | What it covers | Dependencies |
|---|---|---|
| `run_probes.py` | HTTP contract: all stage-1 and stage-2 groups, stage-4 replans, plan application, closures, series amendments, upgrade from stage-3 exports and concurrency, plus stage-3 explanations, policies and selection, accepted terms and revisions, `expected_revision`, history and decision, recurring series, collective moves under policies, restaurant revision, upgrade from genuine stage-1 and stage-2 exports, stage-3 round trip and concurrency | Python 3.11+, standard library only |
| `run_browser_probes.py` | The four screens, data-testid contract, out-of-order / 409 / lost-response recovery, upgrade in the browser, viewport and focus rules, and the same screens driven by a published stage-3 policy (UI-POL) | Python 3.11+, Playwright for Python with Chromium |

## Install (browser runner only)

```sh
pip install playwright && python -m playwright install chromium
```

The HTTP runner needs nothing installed.

## Run

Start the stage-4 service, then from this directory:

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

## Stage-3 fixtures and conventions

Stage-3 groups add restaurant `r_pol` (tables Arbour, Bench, Cellar; combinable Bench+Arbour;
manager `u_mgr`), restaurant `r_pol2` (manager `u_mgr2`) and make `u_mgr` a manager of `r_zulu` for
cutoff timing. Policies are published through the API inside each group. The restaurant revision
is read from the `revision` field of `GET /restaurants/{id}`, as the coordinator defined it.
DST series probes use the European transitions of next calendar year so they never fall in the
past.

`fixtures/stage2_export.json` is a genuine `GET /_test/export` captured from the accepted stage-2
image (revision cbafad2) after seeding a pair and a cancelled single, signing up a user, booking a
pair, a single and a later-cancelled table, recording a lost-response key, failing one key and
running a batch. `fixtures/stage2_export_expected.json` records the tokens, keys, bodies and
original responses. The UPG3 group imports both the stage-1 and the stage-2 export into the
stage-3 service. These files hold only throwaway probe credentials.

## Stage-4 fixtures and conventions

Stage-4 groups (RPV, OPT, LIM, APP, CLS, SAM, UPG4, RT4, CON4 over HTTP; UI-PLAN in the browser)
add restaurant `r_plan` with per-case tables, pairs and seeded bookings whose references are chosen
so the reference order is known; `r_combo` gains manager `u_mgr` for closure and browser checks.
Every optimisation case is hand-computed and listed in CHECKLIST.md Part D with its expected plan.
The restaurant revision starts at 0 (stage 4), so RREV-1, RREV-19 and UPG3-7 now expect 0.

`fixtures/stage3_export.json` is a genuine export from the accepted stage-3 image (revision
2586c6b) with a managed restaurant, a policy, an amended booking, a series with an exception and a
cancelled occurrence, a batch receipt, a lost-response key and a failed key;
`fixtures/stage3_export_expected.json` records tokens, keys, bodies, original responses,
histories, the series and the restaurant revision. Throwaway probe credentials only.

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
