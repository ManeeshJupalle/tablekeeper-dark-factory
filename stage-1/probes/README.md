# Tablekeeper stage 1 - auditor probes

Black-box HTTP probes derived from the stage-1 requirements alone. They never read
the implementation; they talk to a running service and assert exact statuses, error
codes and bodies. `CHECKLIST.md` lists every contract item and the probe id that
covers it.

## Dependencies

Python 3.11 or newer, standard library only (`http.client`, `json`, `threading`,
`datetime`). Nothing to install. The probes do not need `zoneinfo`; all expected
offsets for the 2026 DST dates are fixed from the requirements.

## Run

Start the service (for example with the reviewer's `docker run ... -e PORT=8080 -p 8080:8080 ...`),
then from this directory:

```sh
python3 run_probes.py --base-url http://127.0.0.1:8080
```

`--base-url` defaults to `http://127.0.0.1:8080`; the environment variable
`TABLEKEEPER_BASE_URL` is used when the flag is omitted. `--only RES,IDEM` runs a
subset of groups (the `CONV` summary group always runs).

Output is one line per check:

```
PASS RES-1 201 body fields, status confirmed, exact starts_at/ends_at
FAIL IDEM-5 same key, different body is 409 idempotency_key_reuse -- expected 409 idempotency_key_reuse, got <201 '...'>
```

followed by `# <n> passed, <m> failed`. Exit status is 0 only when every check passes.

## What the run does

Every group starts with `POST /_test/reset` using the probes' own fixtures
(restaurants `r_lumen`, `r_pier`, `r_nacht`, `r_gotham`, `r_zulu`; users `u_hana`,
`u_ravi`, `u_mira`). Far-future dates are computed relative to the run date (first
Thursday at least 21 days ahead) so cutoffs never interfere except where the cutoff is
the thing under test. Cutoff probes use `r_zulu` (zone `Etc/UTC`, 5-minute slots,
60-minute cutoff) with bookings about 48 and 68 minutes ahead of the probe host clock,
so the host and container clocks must agree to within a few minutes. The 2026 DST
dates from the requirements are used verbatim.

Timeouts are 5 s per request and 10 s for `/_test/reset`, `/_test/import` and
`/_test/export`. Concurrency groups fire 17 to 24 simultaneous requests, within the
50 in-flight limit.

Groups: `HLT` health, `RST` reset/seed, `AUTH`, `PUB` restaurants/availability,
`RES` create, `IDEM` idempotency, `LIST`, `CXL` cancel, `PAT` amend, `TIME` zones and
DST, `MOV` atomic reservation moves, `CONC` concurrency, `RT` export/import round trip,
`CONV` run-wide conventions (content type, error shape, no 5xx, timestamp format).

## Known limits

- The cutoff boundary instant itself (`now == starts_at - cutoff`) cannot be hit
  deterministically from outside; the probes test about 12 minutes either side.
- If the probe host clock is within 10 minutes of midnight UTC the `r_zulu` cutoff
  bookings may fall outside its opening hours (`00:00-23:59`); rerun a few minutes later.
- The requirements do not name the success status of `PATCH /reservations/{reference}`;
  the probes expect 200, matching cancel in the same section.
