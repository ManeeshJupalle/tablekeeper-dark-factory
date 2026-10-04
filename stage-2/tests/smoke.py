#!/usr/bin/env python3
"""End-to-end checks for Tablekeeper stage 2 (stage-1 rules included) against a running service.

Usage: python3 tests/smoke.py http://127.0.0.1:8080 [http://stage1-service:8080]

The optional second URL is a running stage-1 service; its export is imported here
to check the upgrade path.

Standard library only. Every requirement in the stage-1 document is exercised at
least once: each endpoint, each error code, the DST rules, idempotent replay,
export/import round trips and the concurrency invariants (50 in flight).
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import threading
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

BASE = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://127.0.0.1:8080"
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
FAILURES: list[str] = []
PASSES = 0


class Resp:
    def __init__(self, status: int, body: bytes):
        self.status = status
        self.body = body

    def json(self):
        return json.loads(self.body.decode("utf-8"))

    def code(self):
        try:
            return self.json()["error"]["code"]
        except Exception:
            return None


def call(method: str, path: str, *, body=None, raw: bytes | None = None, token: str | None = None,
         key: str | None = None, headers: dict | None = None) -> Resp:
    hdrs = {"Content-Type": "application/json"}
    if token:
        hdrs["Authorization"] = f"Bearer {token}"
    if key is not None:
        hdrs["Idempotency-Key"] = key
    hdrs.update(headers or {})
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return Resp(r.status, r.read())
    except urllib.error.HTTPError as e:
        return Resp(e.code, e.read())


def check(name: str, cond: bool, detail: str = ""):
    global PASSES
    if cond:
        PASSES += 1
    else:
        FAILURES.append(f"{name}: {detail}")
        print(f"FAIL {name}: {detail}")


def expect(name: str, r: Resp, status: int, code: str | None = None):
    ok = r.status == status and (code is None or r.code() == code)
    check(name, ok, f"expected {status} {code or ''}, got {r.status} {r.body[:200]!r}")
    return r


def new_key() -> str:
    return uuid.uuid4().hex


# ---- fixtures ---------------------------------------------------------------

ADA = {"id": "u_ada", "email": "ada@example.com", "password": "correct horse", "display_name": "Ada"}
BOB = {"id": "u_bob", "email": "bob@example.com", "password": "correct horse", "display_name": "Bob"}


def all_week(opens="18:00", closes="23:00"):
    return [{"weekday": d, "opens": opens, "closes": closes} for d in WEEKDAYS]


def restaurant(rid="r_anker", **over):
    r = {"id": rid, "name": "Zum Anker", "timezone": "Europe/Berlin", "slot_minutes": 30,
         "reservation_duration_minutes": 90, "cancellation_cutoff_minutes": 120,
         "opening_hours": all_week(),
         "tables": [{"id": "t_1", "label": "1", "capacity": 2},
                    {"id": "t_2", "label": "2", "capacity": 4},
                    {"id": "t_3", "label": "3", "capacity": 6}]}
    r.update(over)
    return r


def fixture(users=None, restaurants=None, reservations=None):
    return {"users": [ADA, BOB] if users is None else users,
            "restaurants": [restaurant()] if restaurants is None else restaurants,
            "reservations": reservations or []}


def reset(fx) -> Resp:
    return call("POST", "/_test/reset", body=fx)


def login(email, password) -> str:
    r = expect(f"login {email}", call("POST", "/auth/login", body={"email": email, "password": password}), 200)
    return r.json()["token"]


def booking_date(tz="Europe/Berlin", lead=7) -> str:
    return (dt.datetime.now(ZoneInfo(tz)).date() + dt.timedelta(days=lead)).isoformat()


def book(token, *, rid="r_anker", table="t_2", at=None, party=4, key=None, **extra) -> Resp:
    body = {"restaurant_id": rid, "table_id": table, "starts_at_local": at or f"{DATE}T19:00",
            "party_size": party}
    body.update(extra)
    return call("POST", "/reservations", body=body, token=token, key=key or new_key())


def availability(rid, date, party, token=None, extra=""):
    return call("GET", f"/availability?restaurant_id={rid}&date={date}&party_size={party}{extra}", token=token)


def slot_tables(rid, date, party, hhmm):
    slots = availability(rid, date, party).json()["slots"]
    return next(s["available_table_ids"] for s in slots if s["starts_at_local"].endswith(hhmm))


DATE = booking_date()


# ---- sections ----------------------------------------------------------------

def test_health_and_routing():
    expect("health", call("GET", "/health"), 200)
    check("health body", call("GET", "/health").json() == {"status": "ok"})
    expect("unknown route", call("GET", "/nope"), 404, "not_found")
    expect("bad method", call("DELETE", "/health"), 405, "method_not_allowed")
    expect("non-json content type", call("POST", "/auth/login", raw=b"hello", headers={"Content-Type": "text/plain"}), 400, "malformed_request")
    expect("huge body", call("POST", "/auth/login", raw=b"[" * (2 * 1024 * 1024)), 413)
    expect("json array body", call("POST", "/auth/login", raw=b"[1,2]"), 400, "malformed_request")
    expect("trailing garbage", call("POST", "/auth/login", raw=b'{"a":1} x'), 400, "malformed_request")


def test_reset_and_auth():
    expect("reset", reset(fixture()), 204)
    expect("reset bad json", call("POST", "/_test/reset", raw=b"{nope"), 400, "malformed_request")
    expect("reset long id", reset(fixture(users=[{**ADA, "id": "u" * 65}])), 422, "validation_failed")
    expect("reset bad tz", reset(fixture(restaurants=[restaurant(timezone="Mars/Olympus")])), 422, "validation_failed")
    for ref in ("x", "lower01", "TOO-LONG-WITH-DASH"):
        expect(f"reset bad ref {ref}", reset(fixture(reservations=[seed(DATE, reference=ref)])), 422, "validation_failed")
    expect("reset after rejects", reset(fixture()), 204)

    ada = login(ADA["email"], ADA["password"])
    bob = login(BOB["email"], BOB["password"])
    r = expect("signup", call("POST", "/auth/signup", body={"email": "new@example.com", "password": "correct horse", "display_name": "New"}), 201)
    body = r.json()
    check("signup shape", body["display_name"] == "New" and body["token"] and body["user_id"])
    expect("signup token works", call("GET", "/reservations", token=body["token"]), 200)
    expect("signup token works twice (second login)", call("POST", "/auth/login", body={"email": "new@example.com", "password": "correct horse"}), 200)
    expect("email taken", call("POST", "/auth/signup", body={"email": ADA["email"], "password": "correct horse", "display_name": "X"}), 409, "email_taken")
    expect("short password", call("POST", "/auth/signup", body={"email": "s@example.com", "password": "1234567", "display_name": "X"}), 422, "validation_failed")
    expect("bad email", call("POST", "/auth/signup", body={"email": "not-an-email", "password": "correct horse", "display_name": "X"}), 422, "validation_failed")
    expect("email wrong type", call("POST", "/auth/signup", body={"email": 17, "password": "correct horse", "display_name": "X"}), 400, "malformed_request")
    expect("missing password", call("POST", "/auth/signup", body={"email": "m@example.com"}), 422, "validation_failed")
    expect("long password ok", call("POST", "/auth/signup", body={"email": "long@example.com", "password": "x" * 200, "display_name": "L"}), 201)
    expect("long password login", call("POST", "/auth/login", body={"email": "long@example.com", "password": "x" * 200}), 200)
    expect("wrong password", call("POST", "/auth/login", body={"email": ADA["email"], "password": "wrong password"}), 401, "unauthenticated")
    expect("unknown email", call("POST", "/auth/login", body={"email": "nobody@example.com", "password": "correct horse"}), 401, "unauthenticated")
    expect("login wrong type", call("POST", "/auth/login", body={"email": ADA["email"], "password": 5}), 400, "malformed_request")
    expect("no token list", call("GET", "/reservations"), 401, "unauthenticated")
    expect("no token get", call("GET", "/reservations/ABC123"), 401, "unauthenticated")
    expect("bad token", call("GET", "/reservations", token="not-a-real-token"), 401, "unauthenticated")
    expect("malformed auth header", call("GET", "/reservations", headers={"Authorization": "Basic abc"}), 401, "unauthenticated")
    expect("no token post", call("POST", "/reservations", body={}, key=new_key()), 401, "unauthenticated")
    expect("no token moves", call("POST", "/reservation-moves", body={"moves": []}, key=new_key()), 401, "unauthenticated")
    expect("no token cancel", call("POST", "/reservations/ABC123/cancel"), 401, "unauthenticated")
    expect("no token patch", call("PATCH", "/reservations/ABC123", body={}), 401, "unauthenticated")
    expect("public restaurants", call("GET", "/restaurants"), 200)
    expect("public detail", call("GET", "/restaurants/r_anker"), 200)
    expect("public availability", availability("r_anker", DATE, 2), 200)


def seed(date, *, reference="SEED01", table_id="t_2", at="19:00", user_id="u_ada", party_size=4, **extra):
    return {"id": "res_seed", "reference": reference, "user_id": user_id, "restaurant_id": "r_anker",
            "table_id": table_id, "starts_at_local": f"{date}T{at}", "party_size": party_size, **extra}


def test_restaurants_and_availability():
    reset(fixture())
    body = call("GET", "/restaurants").json()
    check("restaurant list", body["restaurants"] == [{"id": "r_anker", "name": "Zum Anker", "timezone": "Europe/Berlin"}], str(body))
    detail = call("GET", "/restaurants/r_anker").json()
    check("detail shape", detail == {**restaurant(), "combinable": []}, json.dumps(detail))
    expect("unknown restaurant", call("GET", "/restaurants/r_nope"), 404, "not_found")

    av = availability("r_anker", DATE, 2).json()
    times = [s["starts_at_local"].split("T")[1] for s in av["slots"]]
    check("slot grid", times == ["18:00", "18:30", "19:00", "19:30", "20:00", "20:30", "21:00", "21:30"], str(times))
    check("availability header", av["restaurant_id"] == "r_anker" and av["date"] == DATE and av["timezone"] == "Europe/Berlin")
    check("slot starts_at", av["slots"][0]["starts_at"].startswith(f"{DATE}T18:00:00+0"), av["slots"][0]["starts_at"])
    check("all tables party 2", av["slots"][0]["available_table_ids"] == ["t_1", "t_2", "t_3"])
    check("capacity filter 4", slot_tables("r_anker", DATE, 4, "18:00") == ["t_2", "t_3"])
    check("capacity filter 6", slot_tables("r_anker", DATE, 6, "18:00") == ["t_3"])
    check("capacity filter 7", slot_tables("r_anker", DATE, 7, "18:00") == [])
    for drop in ("restaurant_id", "date", "party_size"):
        params = {"restaurant_id": "r_anker", "date": DATE, "party_size": "2"}
        params.pop(drop)
        q = "&".join(f"{k}={v}" for k, v in params.items())
        expect(f"missing {drop}", call("GET", f"/availability?{q}"), 422, "validation_failed")
    for bad in ("2026-02-30", "not-a-date", "24-09-2026", "2026-9-4", "2026-09-04T00:00"):
        expect(f"bad date {bad}", availability("r_anker", bad, 2), 422, "validation_failed")
    for bad in ("0", "-1", "abc", "1e9", "4.0", "+4", "%204", ""):
        expect(f"bad party {bad!r}", availability("r_anker", DATE, bad), 422, "validation_failed")
    expect("availability unknown restaurant", availability("r_nope", DATE, 2), 404, "not_found")
    expect("unknown query params ignored", availability("r_anker", DATE, 2, extra="&sort=x&page=3"), 200)

    # closed day
    wd = WEEKDAYS[dt.date.fromisoformat(DATE).weekday()]
    closed = [h for h in all_week() if h["weekday"] != wd]
    reset(fixture(restaurants=[restaurant(opening_hours=closed)]))
    check("closed day", availability("r_anker", DATE, 2).json()["slots"] == [])
    reset(fixture())


def test_reservations():
    reset(fixture())
    ada = login(ADA["email"], ADA["password"])
    bob = login(BOB["email"], BOB["password"])
    r = expect("create", book(ada), 201).json()
    check("create shape", r["restaurant_id"] == "r_anker" and r["table_id"] == "t_2" and r["party_size"] == 4
          and r["status"] == "confirmed" and r["starts_at_local"] == f"{DATE}T19:00" and r["reservation_id"]
          and r["created_at"].endswith("+00:00"), json.dumps(r))
    import re
    check("reference format", re.fullmatch(r"[A-Z0-9]{6,12}", r["reference"]) is not None, r["reference"])
    check("starts_at", r["starts_at"].startswith(f"{DATE}T19:00:00+0"), r["starts_at"])
    check("ends_at", r["ends_at"].startswith(f"{DATE}T20:30:00+0"), r["ends_at"])
    ref = r["reference"]

    expect("double booking", book(ada), 409, "table_unavailable")
    expect("overlap 20:00", book(ada, at=f"{DATE}T20:00"), 409, "table_unavailable")
    expect("overlap 18:00 (ends 19:30)", book(ada, at=f"{DATE}T18:00"), 409, "table_unavailable")
    expect("adjacent 20:30", book(ada, at=f"{DATE}T20:30"), 201)
    expect("other table same time", book(ada, table="t_3"), 201)
    expect("off grid 19:15", book(ada, at=f"{DATE}T19:15"), 422, "not_on_slot_grid")
    expect("off grid 18:01", book(ada, at=f"{DATE}T18:01"), 422, "not_on_slot_grid")
    expect("before opening", book(ada, at=f"{DATE}T17:00"), 422, "outside_opening_hours")
    expect("at closing", book(ada, at=f"{DATE}T23:00", table="t_1", party=2), 422, "outside_opening_hours")
    expect("after closing", book(ada, at=f"{DATE}T23:30"), 422, "outside_opening_hours")
    expect("ends after closing", book(ada, at=f"{DATE}T22:00", table="t_1", party=2), 422, "outside_opening_hours")
    expect("last valid slot", book(ada, at=f"{DATE}T21:30", table="t_1", party=2), 201)
    expect("capacity", book(ada, table="t_1", party=4, at=f"{DATE}T18:00"), 422, "party_exceeds_capacity")
    for party in (0, -1, "4", 1.5, True, None, 4.0, 1e9):
        expect(f"bad party {party!r}", book(ada, party=party, table="t_3", at=f"{DATE}T21:30"), 422, "validation_failed")
    expect("missing party", call("POST", "/reservations", body={"restaurant_id": "r_anker", "table_id": "t_3", "starts_at_local": f"{DATE}T21:30"}, token=ada, key=new_key()), 422, "validation_failed")
    expect("missing table", call("POST", "/reservations", body={"restaurant_id": "r_anker", "party_size": 2, "starts_at_local": f"{DATE}T21:30"}, token=ada, key=new_key()), 422, "validation_failed")
    expect("table wrong type", book(ada, table=7), 400, "malformed_request")
    expect("restaurant wrong type", book(ada, rid=["r_anker"]), 400, "malformed_request")
    expect("unknown restaurant", book(ada, rid="r_nope"), 404, "not_found")
    expect("unknown table", book(ada, table="t_nope"), 404, "not_found")
    for v in ("2026-09-24T19:00:00+02:00", "2026-09-24T19:00Z", "2026-09-24T19:00:00", "not-a-time", "2026-02-30T19:00", "2026-09-24T24:00", 5):
        expect(f"bad starts_at_local {v!r}", call(
            "POST", "/reservations", body={"restaurant_id": "r_anker", "table_id": "t_3", "starts_at_local": v, "party_size": 2},
            token=ada, key=new_key()), 422, "validation_failed")
    expect("malformed json", call("POST", "/reservations", raw=b"{not json", token=ada, key=new_key()), 400, "malformed_request")
    expect("unknown fields ignored", book(ada, at=f"{DATE}T18:00", table="t_1", party=2, souvenir="yes"), 201)
    expect("missing key", call("POST", "/reservations", body={"restaurant_id": "r_anker", "table_id": "t_3", "starts_at_local": f"{DATE}T21:30", "party_size": 2}, token=ada), 400, "missing_idempotency_key")
    expect("empty key", call("POST", "/reservations", body={"restaurant_id": "r_anker", "table_id": "t_3", "starts_at_local": f"{DATE}T21:30", "party_size": 2}, token=ada, key=""), 400, "missing_idempotency_key")
    expect("long key", book(ada, key="k" * 256), 422, "validation_failed")
    expect("255 key ok", book(ada, key="k" * 255, table="t_3", at=f"{DATE}T21:30", party=2), 201)
    expect("past date bookable", book(ada, at="2020-01-01T19:00", table="t_3", party=2), 201)

    # table from another restaurant
    other = restaurant("r_other", name="Other", tables=[{"id": "t_x", "label": "X", "capacity": 4}])
    reset(fixture(restaurants=[restaurant(), other]))
    ada = login(ADA["email"], ADA["password"])
    bob = login(BOB["email"], BOB["password"])
    expect("table of other restaurant", book(ada, table="t_x"), 404, "not_found")
    expect("same table id in other restaurant independent", book(ada, rid="r_other", table="t_x"), 201)

    # reads
    reset(fixture())
    ada = login(ADA["email"], ADA["password"])
    bob = login(BOB["email"], BOB["password"])
    a1 = book(ada, at=f"{DATE}T18:00").json()
    a2 = book(ada, at=f"{DATE}T21:00").json()
    a3 = book(ada, at=f"{DATE}T19:30", table="t_3").json()
    b1 = book(bob, at=f"{DATE}T19:30").json()
    mine = call("GET", "/reservations", token=ada).json()["reservations"]
    check("list only mine", [m["reference"] for m in mine] == [a2["reference"], a3["reference"], a1["reference"]], str([m["starts_at"] for m in mine]))
    check("list shape", mine[0] == a2, json.dumps(mine[0]))
    check("empty list", call("GET", "/reservations", token=login("bob@example.com", "correct horse")).json()["reservations"] == [b1])
    check("get by ref", call("GET", f"/reservations/{a1['reference']}", token=ada).json() == a1)
    expect("other's ref 404", call("GET", f"/reservations/{b1['reference']}", token=ada), 404, "not_found")
    expect("unknown ref", call("GET", "/reservations/ZZZZZZ", token=ada), 404, "not_found")

    # cancel
    c = expect("cancel", call("POST", f"/reservations/{a1['reference']}/cancel", token=ada), 200).json()
    check("cancel status", c["status"] == "cancelled" and c["reference"] == a1["reference"])
    check("cancel frees table", "t_2" in slot_tables("r_anker", DATE, 4, "18:00"))
    c2 = expect("cancel twice", call("POST", f"/reservations/{a1['reference']}/cancel", token=ada), 200).json()
    check("cancel twice body", c2 == c)
    check("list includes cancelled", "cancelled" in {m["status"] for m in call("GET", "/reservations", token=ada).json()["reservations"]})
    expect("cancel other's", call("POST", f"/reservations/{b1['reference']}/cancel", token=ada), 404, "not_found")
    expect("cancel unknown", call("POST", "/reservations/NOPE12/cancel", token=ada), 404, "not_found")
    expect("rebook after cancel", book(ada, at=f"{DATE}T18:00"), 201)

    # patch
    p = expect("patch time+table", call("PATCH", f"/reservations/{a2['reference']}", body={"starts_at_local": f"{DATE}T21:30", "table_id": "t_3"}, token=ada), 200).json()
    check("patch keeps ids", p["reference"] == a2["reference"] and p["reservation_id"] == a2["reservation_id"] and p["starts_at_local"] == f"{DATE}T21:30" and p["table_id"] == "t_3")
    check("patch frees old slot", "t_2" in slot_tables("r_anker", DATE, 4, "21:00"))
    expect("patch party", call("PATCH", f"/reservations/{a2['reference']}", body={"party_size": 3}, token=ada), 200)
    expect("patch empty body noop", call("PATCH", f"/reservations/{a2['reference']}", body={}, token=ada), 200)
    expect("patch own overlap ok (move 21:30 -> 21:00 same table)", call("PATCH", f"/reservations/{a2['reference']}", body={"starts_at_local": f"{DATE}T21:00"}, token=ada), 200)
    expect("patch off grid", call("PATCH", f"/reservations/{a2['reference']}", body={"starts_at_local": f"{DATE}T19:15"}, token=ada), 422, "not_on_slot_grid")
    expect("patch capacity", call("PATCH", f"/reservations/{a2['reference']}", body={"table_id": "t_1"}, token=ada), 422, "party_exceeds_capacity")
    expect("patch unknown table", call("PATCH", f"/reservations/{a2['reference']}", body={"table_id": "t_nope"}, token=ada), 404, "not_found")
    expect("patch bad party", call("PATCH", f"/reservations/{a2['reference']}", body={"party_size": "4"}, token=ada), 422, "validation_failed")
    expect("patch table wrong type", call("PATCH", f"/reservations/{a2['reference']}", body={"table_id": 4}, token=ada), 400, "malformed_request")
    expect("patch malformed", call("PATCH", f"/reservations/{a2['reference']}", raw=b"{", token=ada), 400, "malformed_request")
    expect("patch onto taken table", call("PATCH", f"/reservations/{a2['reference']}", body={"table_id": "t_3", "starts_at_local": f"{DATE}T19:30"}, token=ada), 409, "table_unavailable")
    after = call("GET", f"/reservations/{a2['reference']}", token=ada).json()
    check("failed patch leaves original", after["table_id"] == "t_3" and after["starts_at_local"] == f"{DATE}T21:00" and after["party_size"] == 3, json.dumps(after))
    expect("patch other's", call("PATCH", f"/reservations/{b1['reference']}", body={"party_size": 2}, token=ada), 404, "not_found")
    expect("patch cancelled", call("PATCH", f"/reservations/{a1['reference']}", body={"party_size": 2}, token=ada), 409, "reservation_cancelled")
    expect("patch unknown", call("PATCH", "/reservations/NOPE12", body={"party_size": 2}, token=ada), 404, "not_found")

    # cutoff
    reset(fixture(restaurants=[restaurant(cancellation_cutoff_minutes=60 * 24 * 3650)]))
    ada = login(ADA["email"], ADA["password"])
    ref = book(ada).json()["reference"]
    expect("cancel inside cutoff", call("POST", f"/reservations/{ref}/cancel", token=ada), 409, "cutoff_passed")
    expect("patch inside cutoff", call("PATCH", f"/reservations/{ref}", body={"table_id": "t_3"}, token=ada), 409, "cutoff_passed")
    expect("moves inside cutoff", call("POST", "/reservation-moves", body={"moves": [{"reference": ref, "table_id": "t_3"}]}, token=ada, key=new_key()), 409, "cutoff_passed")
    yesterday = (dt.date.fromisoformat(DATE) - dt.timedelta(days=8)).isoformat()
    reset(fixture(reservations=[seed(yesterday)]))
    ada = login(ADA["email"], ADA["password"])
    expect("cancel past booking", call("POST", "/reservations/SEED01/cancel", token=ada), 409, "cutoff_passed")


def test_seeded():
    reset(fixture(reservations=[seed(DATE)]))
    ada = login(ADA["email"], ADA["password"])
    bob = login(BOB["email"], BOB["password"])
    check("seed occupies", "t_2" not in slot_tables("r_anker", DATE, 4, "19:00"))
    body = expect("seed get", call("GET", "/reservations/SEED01", token=ada), 200).json()
    check("seed shape", body["reference"] == "SEED01" and body["status"] == "confirmed" and body["reservation_id"] == "res_seed")
    expect("seed other 404", call("GET", "/reservations/SEED01", token=bob), 404, "not_found")
    expect("seed blocks bob", book(bob), 409, "table_unavailable")
    expect("seed cancel", call("POST", "/reservations/SEED01/cancel", token=ada), 200)
    check("seed cancel frees", "t_2" in slot_tables("r_anker", DATE, 4, "19:00"))
    reset(fixture(reservations=[seed(DATE, status="cancelled")]))
    check("seeded cancelled does not occupy", "t_2" in slot_tables("r_anker", DATE, 4, "19:00"))


def test_idempotency():
    reset(fixture())
    ada = login(ADA["email"], ADA["password"])
    bob = login(BOB["email"], BOB["password"])
    key = new_key()
    first = expect("first use", book(ada, key=key), 201).json()
    replay = expect("replay", book(ada, key=key), 200).json()
    check("replay body", replay == first)
    expect("replay different body", book(ada, key=key, party=3), 409, "idempotency_key_reuse")
    expect("replay different invalid body", book(ada, key=key, party="x"), 409, "idempotency_key_reuse")
    # same JSON value with different key order / whitespace
    raw = ('{"party_size": 4, "table_id": "t_2",   "starts_at_local": "%sT19:00", "restaurant_id": "r_anker"}' % DATE).encode()
    expect("replay reordered", call("POST", "/reservations", raw=raw, token=ada, key=key), 200)
    expect("same key other user", book(bob, key=key, table="t_3"), 201)
    expect("same key other path", call("POST", "/reservation-moves", body={"moves": [{"reference": first["reference"]}]}, token=ada, key=key), 201)
    # failed first use leaves key reusable
    k2 = new_key()
    expect("failed first use", book(ada, key=k2, table="t_2", at=f"{DATE}T19:00"), 409, "table_unavailable")
    expect("key reusable after failure", book(ada, key=k2, table="t_1", party=2, at=f"{DATE}T18:00"), 201)
    # replay after cancel returns original
    call("POST", f"/reservations/{first['reference']}/cancel", token=ada)
    again = expect("replay after cancel", book(ada, key=key), 200).json()
    check("replay after cancel is original", again == first and again["status"] == "confirmed")


def test_dst():
    berlin = restaurant("r_berlin", timezone="Europe/Berlin", opening_hours=all_week("00:00", "23:30"))
    ny = restaurant("r_ny", timezone="America/New_York", opening_hours=all_week("00:00", "23:30"))
    reset(fixture(restaurants=[berlin, ny]))
    ada = login(ADA["email"], ADA["password"])
    expect("spring gap", book(ada, rid="r_berlin", at="2026-03-29T02:30"), 422, "invalid_local_time")
    expect("spring gap 02:00", book(ada, rid="r_berlin", at="2026-03-29T02:00"), 422, "invalid_local_time")
    expect("spring gap NY", book(ada, rid="r_ny", at="2026-03-08T02:30"), 422, "invalid_local_time")
    times = [s["starts_at_local"].split("T")[1] for s in availability("r_berlin", "2026-03-29", 4).json()["slots"]]
    check("gap not in availability", "02:00" not in times and "02:30" not in times and "03:00" in times and "01:30" in times, str(times))
    times = [s["starts_at_local"].split("T")[1] for s in availability("r_berlin", "2026-10-25", 4).json()["slots"]]
    check("fallback once", times.count("02:00") == 1 and times.count("02:30") == 1, str(times))
    check("fallback slot count", len(times) == len(set(times)))
    slots = availability("r_berlin", "2026-10-25", 4).json()["slots"]
    two = next(s for s in slots if s["starts_at_local"].endswith("02:00"))
    check("fallback slot offset", two["starts_at"] == "2026-10-25T02:00:00+02:00", two["starts_at"])
    b = expect("fallback book", book(ada, rid="r_berlin", at="2026-10-25T02:00"), 201).json()
    check("fallback first occurrence", b["starts_at"] == "2026-10-25T02:00:00+02:00", b["starts_at"])
    check("fallback ends_at absolute", b["ends_at"] == "2026-10-25T02:30:00+01:00", b["ends_at"])
    b = expect("fallback 01:30", book(ada, rid="r_berlin", at="2026-10-25T01:30", table="t_3"), 201).json()
    check("duration absolute", b["ends_at"] == "2026-10-25T02:00:00+01:00", b["ends_at"])
    expect("second occurrence not bookable (overlap with first)", book(ada, rid="r_berlin", at="2026-10-25T02:30"), 409, "table_unavailable")
    expect("after fallback 04:00 free", book(ada, rid="r_berlin", at="2026-10-25T04:00"), 201)
    ny1 = expect("NY fallback 01:30", book(ada, rid="r_ny", at="2026-11-01T01:30"), 201).json()
    check("NY first occurrence", ny1["starts_at"] == "2026-11-01T01:30:00-04:00" and ny1["ends_at"] == "2026-11-01T02:00:00-05:00", json.dumps(ny1))
    before = book(ada, rid="r_berlin", at="2026-10-25T00:00", table="t_1", party=2).json()
    after = book(ada, rid="r_berlin", at="2026-10-25T12:00", table="t_1", party=2).json()
    check("offsets both sides", before["starts_at"].endswith("+02:00") and after["starts_at"].endswith("+01:00"))
    bb = book(ada, rid="r_berlin", at="2026-12-01T18:00", table="t_3").json()
    nn = book(ada, rid="r_ny", at="2026-12-01T12:00", table="t_3").json()
    check("same instant", dt.datetime.fromisoformat(bb["starts_at"]) == dt.datetime.fromisoformat(nn["starts_at"]))
    # UTC-zone restaurant writes +00:00 not Z
    london = restaurant("r_ldn", timezone="Europe/London", opening_hours=all_week("00:00", "23:30"))
    reset(fixture(restaurants=[london]))
    ada = login(ADA["email"], ADA["password"])
    l = book(ada, rid="r_ldn", at="2026-12-01T18:00").json()
    check("london offset", l["starts_at"] == "2026-12-01T18:00:00+00:00", l["starts_at"])


def test_moves():
    reset(fixture(restaurants=[restaurant(), restaurant("r_other", name="Other")]))
    ada = login(ADA["email"], ADA["password"])
    bob = login(BOB["email"], BOB["password"])
    a = book(ada, table="t_1", party=2, at=f"{DATE}T19:00").json()
    b = book(ada, table="t_2", party=2, at=f"{DATE}T19:00").json()
    c = book(ada, table="t_3", party=2, at=f"{DATE}T21:00").json()
    o = book(ada, rid="r_other", table="t_1", party=2).json()
    bobs = book(bob, table="t_3", party=4, at=f"{DATE}T18:00").json()
    mv = lambda moves, key=None, token=ada: call("POST", "/reservation-moves", body={"moves": moves}, token=token, key=key or new_key())
    # swap
    key = new_key()
    r = expect("swap", mv([{"reference": a["reference"], "table_id": "t_2"}, {"reference": b["reference"], "table_id": "t_1"}], key), 201).json()
    check("swap result", [x["table_id"] for x in r["reservations"]] == ["t_2", "t_1"] and [x["reference"] for x in r["reservations"]] == [a["reference"], b["reference"]], json.dumps(r))
    check("swap persisted", call("GET", f"/reservations/{a['reference']}", token=ada).json()["table_id"] == "t_2")
    rep = expect("swap replay", mv([{"reference": a["reference"], "table_id": "t_2"}, {"reference": b["reference"], "table_id": "t_1"}], key), 200).json()
    check("swap replay body", rep == r)
    expect("swap key reuse", mv([{"reference": a["reference"], "table_id": "t_3"}], key), 409, "idempotency_key_reuse")
    expect("moves missing key", call("POST", "/reservation-moves", body={"moves": [{"reference": a["reference"]}]}, token=ada), 400, "missing_idempotency_key")
    # no-op and unchanged items included
    r = expect("noop move", mv([{"reference": c["reference"]}]), 201).json()
    check("noop retains", r["reservations"][0] == call("GET", f"/reservations/{c['reference']}", token=ada).json())
    # shape errors
    for bad in ({"moves": []}, {"moves": [{"reference": a["reference"]}] * 9}, {"moves": "x"}, {}, {"moves": [1]},
                {"moves": [{"reference": 5}]}, {"moves": [{"table_id": "t_1"}]},
                {"moves": [{"reference": a["reference"]}, {"reference": a["reference"]}]}):
        expect(f"moves shape {json.dumps(bad)[:40]}", call("POST", "/reservation-moves", body=bad, token=ada, key=new_key()), 422, "validation_failed")
    expect("moves malformed", call("POST", "/reservation-moves", raw=b"nope", token=ada, key=new_key()), 400, "malformed_request")
    expect("moves unknown ref", mv([{"reference": "NOPE12"}]), 404, "not_found")
    expect("moves other's ref", mv([{"reference": bobs["reference"]}]), 404, "not_found")
    expect("moves mixed restaurants", mv([{"reference": a["reference"]}, {"reference": o["reference"]}]), 422, "validation_failed")
    expect("moves overlap unlisted", mv([{"reference": c["reference"], "table_id": "t_3", "starts_at_local": f"{DATE}T18:30"}]), 409, "table_unavailable")
    expect("moves overlap within batch", mv([{"reference": a["reference"], "table_id": "t_3", "starts_at_local": f"{DATE}T20:00"},
                                             {"reference": b["reference"], "table_id": "t_3", "starts_at_local": f"{DATE}T21:00"}]), 409, "table_unavailable")
    check("failed batch unchanged", call("GET", f"/reservations/{a['reference']}", token=ada).json()["table_id"] == "t_2"
          and call("GET", f"/reservations/{b['reference']}", token=ada).json()["table_id"] == "t_1")
    expect("moves bad party", mv([{"reference": a["reference"], "party_size": 0}]), 422, "validation_failed")
    expect("moves capacity", mv([{"reference": a["reference"], "party_size": 5}]), 422, "party_exceeds_capacity")
    expect("moves off grid", mv([{"reference": a["reference"], "starts_at_local": f"{DATE}T19:10"}]), 422, "not_on_slot_grid")
    expect("moves unknown table", mv([{"reference": a["reference"], "table_id": "t_x"}]), 404, "not_found")
    expect("moves table wrong type", mv([{"reference": a["reference"], "table_id": 1}]), 400, "malformed_request")
    # input-order precedence: item 1 bad party vs item 2 unknown ref -> item 1 error first
    expect("moves order", mv([{"reference": a["reference"], "party_size": -1}, {"reference": "NOPE12"}]), 422, "validation_failed")
    expect("moves order 2", mv([{"reference": "NOPE12"}, {"reference": a["reference"], "party_size": -1}]), 404, "not_found")
    call("POST", f"/reservations/{c['reference']}/cancel", token=ada)
    expect("moves cancelled", mv([{"reference": c["reference"]}]), 409, "reservation_cancelled")
    # move within own occupancy (same table, overlapping new slot)
    expect("moves own overlap", mv([{"reference": a["reference"], "starts_at_local": f"{DATE}T19:30"}]), 201)
    # unknown fields ignored, created_at unchanged
    r = mv([{"reference": a["reference"], "foo": "bar"}]).json()["reservations"][0]
    check("moves created_at unchanged", r["created_at"] == a["created_at"] and r["reservation_id"] == a["reservation_id"])


def test_export_import():
    reset(fixture())
    ada = login(ADA["email"], ADA["password"])
    key = new_key()
    receipt = book(ada, key=key).json()
    mkey = new_key()
    batch = call("POST", "/reservation-moves", body={"moves": [{"reference": receipt["reference"], "table_id": "t_3"}]}, token=ada, key=mkey).json()
    cancelled = book(ada, table="t_1", party=2, at=f"{DATE}T18:00").json()
    call("POST", f"/reservations/{cancelled['reference']}/cancel", token=ada)
    snap = expect("export", call("GET", "/_test/export"), 200).json()
    check("export envelope", snap["track"] == "tablekeeper" and snap["format_version"] == 1 and isinstance(snap["state"], dict))
    listing = call("GET", "/reservations", token=ada).json()
    # a later write must not change the snapshot: take a copy, mutate, compare
    snap_copy = json.loads(json.dumps(snap))
    book(ada, table="t_2", party=2, at=f"{DATE}T21:30")
    check("export immutable", snap == snap_copy)

    reset(fixture(restaurants=[restaurant("r_two", name="Two")]))
    expect("import", call("POST", "/_test/import", body=snap), 204)
    check("import restores restaurants", [r["id"] for r in call("GET", "/restaurants").json()["restaurants"]] == ["r_anker"])
    expect("old token valid", call("GET", "/reservations", token=ada), 200)
    check("import restores reservations", call("GET", "/reservations", token=ada).json() == listing)
    check("import restores receipt", call("GET", f"/reservations/{receipt['reference']}", token=ada).json()["table_id"] == "t_3")
    expect("login after import", call("POST", "/auth/login", body={"email": ADA["email"], "password": ADA["password"]}), 200)
    rep = expect("replay after import", book(ada, key=key), 200).json()
    check("replay after import body", rep == receipt)
    rep = expect("batch replay after import", call("POST", "/reservation-moves", body={"moves": [{"reference": receipt["reference"], "table_id": "t_3"}]}, token=ada, key=mkey), 200).json()
    check("batch replay after import body", rep == batch)
    expect("import twice", call("POST", "/_test/import", body=snap), 204)
    check("import twice no duplicates", call("GET", "/reservations", token=ada).json() == listing)
    check("occupancy after import", "t_3" not in slot_tables("r_anker", DATE, 4, "19:00"))
    # invalid imports leave the destination unchanged
    expect("import bad json", call("POST", "/_test/import", raw=b"{"), 400, "malformed_request")
    expect("import wrong track", call("POST", "/_test/import", body={**snap, "track": "pocketful"}), 422, "validation_failed")
    expect("import wrong version", call("POST", "/_test/import", body={**snap, "format_version": 2}), 422, "validation_failed")
    expect("import missing state", call("POST", "/_test/import", body={"track": "tablekeeper", "format_version": 1}), 422, "validation_failed")
    expect("import bad state", call("POST", "/_test/import", body={**snap, "state": {"users": "nope"}}), 422, "validation_failed")
    broken = json.loads(json.dumps(snap))
    broken["state"]["reservations"][0]["restaurant_id"] = "r_missing"
    expect("import inconsistent state", call("POST", "/_test/import", body=broken), 422, "validation_failed")
    check("destination unchanged", call("GET", "/reservations", token=ada).json() == listing)
    expect("reset clears imported", reset(fixture(restaurants=[restaurant("r_two", name="Two")])), 204)
    expect("token gone after reset", call("GET", "/reservations", token=ada), 401, "unauthenticated")
    check("reset replaced", [r["id"] for r in call("GET", "/restaurants").json()["restaurants"]] == ["r_two"])


def burst(fn, n):
    barrier = threading.Barrier(n)

    def worker(i):
        barrier.wait(timeout=30)
        try:
            return fn(i)
        except Exception as exc:  # noqa: BLE001
            return exc

    with ThreadPoolExecutor(max_workers=n) as pool:
        return list(pool.map(worker, range(n)))


def tally(results):
    counts = {}
    for r in results:
        s = getattr(r, "status", 0)
        counts[s] = counts.get(s, 0) + 1
    return counts


def test_concurrency():
    users = [dict(ADA, id=f"u_{i}", email=f"r{i}@example.com") for i in range(50)]
    reset(fixture(users=users))
    tokens = burst(lambda i: login(users[i]["email"], users[i]["password"]), 50)
    check("50 parallel logins", all(isinstance(t, str) for t in tokens), str(tally(tokens)))
    # 50 different keys, one table and slot
    out = burst(lambda i: book(tokens[i]), 50)
    counts = tally(out)
    check("race: one winner", counts.get(201) == 1 and counts.get(409) == 49 and not any(s >= 500 or s == 0 for s in counts), str(counts))
    codes = {r.code() for r in out if r.status == 409}
    check("race: losers table_unavailable", codes == {"table_unavailable"}, str(codes))
    # 50 identical requests with one key by one user
    key = new_key()
    out = burst(lambda i: book(tokens[0], table="t_3", key=key), 50)
    counts = tally(out)
    bodies = {r.body for r in out}
    check("idempotent race: one 201", counts.get(201) == 1 and counts.get(200) == 49, str(counts))
    check("idempotent race: identical bodies", len(bodies) == 1, str(len(bodies)))
    check("idempotent race: one reservation", len(call("GET", "/reservations", token=tokens[0]).json()["reservations"]) == 1)
    # cancel-and-rebook race
    ref = call("GET", "/reservations", token=tokens[0]).json()["reservations"][0]["reference"]
    out = burst(lambda i: call("POST", f"/reservations/{ref}/cancel", token=tokens[0]) if i == 0 else book(tokens[i], table="t_3"), 50)
    counts = tally(out)
    check("cancel+rebook race: no 5xx, at most one winner", not any(s >= 500 or s == 0 for s in counts) and counts.get(201, 0) <= 1, str(counts))
    # PATCH race onto one free table
    reset(fixture(users=users, restaurants=[restaurant(tables=[{"id": f"t_{i}", "label": str(i), "capacity": 4} for i in range(51)])]))
    tokens = burst(lambda i: login(users[i]["email"], users[i]["password"]), 50)
    refs = burst(lambda i: book(tokens[i], table=f"t_{i + 1}").json()["reference"], 50)
    out = burst(lambda i: call("PATCH", f"/reservations/{refs[i]}", body={"table_id": "t_0"}, token=tokens[i]), 50)
    counts = tally(out)
    check("patch race: one winner", counts.get(200) == 1 and counts.get(409) == 49, str(counts))
    # mixed read load with no 5xx
    out = burst(lambda i: availability("r_anker", DATE, 2) if i % 2 else call("GET", "/reservations", token=tokens[i]), 50)
    check("mixed load no 5xx", set(tally(out)) == {200}, str(tally(out)))
    # moves race: 50 batches moving different bookings onto one table
    out = burst(lambda i: call("POST", "/reservation-moves", body={"moves": [{"reference": refs[i], "table_id": "t_0", "starts_at_local": f"{DATE}T21:30"}]}, token=tokens[i], key=new_key()), 50)
    counts = tally(out)
    check("moves race: one winner", counts.get(201) == 1 and counts.get(409) == 49, str(counts))


# ---- stage 2 ------------------------------------------------------------------

def combo_restaurant(**over):
    return restaurant(combinable=[["t_1", "t_2"], ["t_2", "t_3"]], **over)


def slot_at(rid, date, party, hhmm):
    return next(s for s in availability(rid, date, party).json()["slots"] if s["starts_at_local"].endswith(hhmm))


def test_stage2_api():
    for bad in ([["t_1"]], [["t_1", "t_2", "t_3"]], [["t_1", "t_1"]], [["t_1", "t_x"]], ["t_1"], [[1, 2]], "x"):
        expect(f"s2 bad combinable {json.dumps(bad)}", reset(fixture(restaurants=[restaurant(combinable=bad)])), 422, "validation_failed")
    reset(fixture(restaurants=[combo_restaurant()]))
    ada = login(ADA["email"], ADA["password"])
    bob = login(BOB["email"], BOB["password"])
    detail = call("GET", "/restaurants/r_anker").json()
    check("s2 detail combinable", detail["combinable"] == [["t_1", "t_2"], ["t_2", "t_3"]], json.dumps(detail["combinable"]))

    # availability options
    slot = slot_at("r_anker", DATE, 2, "19:00")
    check("s2 options party 2", slot["available_options"] == [
        {"table_ids": ["t_1"], "capacity": 2}, {"table_ids": ["t_2"], "capacity": 4}, {"table_ids": ["t_3"], "capacity": 6},
        {"table_ids": ["t_1", "t_2"], "capacity": 6}, {"table_ids": ["t_2", "t_3"], "capacity": 10}], json.dumps(slot["available_options"]))
    check("s2 available_table_ids unchanged", slot["available_table_ids"] == ["t_1", "t_2", "t_3"])
    slot = slot_at("r_anker", DATE, 7, "19:00")
    check("s2 options party 7", slot["available_options"] == [{"table_ids": ["t_2", "t_3"], "capacity": 10}] and slot["available_table_ids"] == [], json.dumps(slot))

    # response shape: table_ids always, table_id only for singles
    post = lambda b, token=ada: call("POST", "/reservations", body=b, token=token, key=new_key())
    body = lambda **o: {"restaurant_id": "r_anker", "starts_at_local": f"{DATE}T21:30", "party_size": 2, **o}
    single = expect("s2 single via table_id", book(ada, table="t_1", party=2, at=f"{DATE}T18:00"), 201).json()
    check("s2 single shape", single["table_ids"] == ["t_1"] and single["table_id"] == "t_1", json.dumps(single))
    single2 = expect("s2 single via table_ids", post(body(table_ids=["t_3"], starts_at_local=f"{DATE}T18:00")), 201).json()
    check("s2 single2 shape", single2["table_ids"] == ["t_3"] and single2["table_id"] == "t_3")
    slot = slot_at("r_anker", DATE, 2, "18:00")
    check("s2 single blocks pair", [o["table_ids"] for o in slot["available_options"]] == [["t_2"]], json.dumps(slot["available_options"]))
    expect("s2 pair blocked by single", post(body(table_ids=["t_1", "t_2"], starts_at_local=f"{DATE}T18:00", party_size=5), bob), 409, "table_unavailable")
    combo = expect("s2 pair ok", post(body(table_ids=["t_2", "t_1"], starts_at_local=f"{DATE}T20:00", party_size=6)), 201).json()
    check("s2 pair shape (combinable order, no table_id)", combo["table_ids"] == ["t_1", "t_2"] and "table_id" not in combo, json.dumps(combo))
    check("s2 pair in list", any(r["table_ids"] == ["t_1", "t_2"] and "table_id" not in r for r in call("GET", "/reservations", token=ada).json()["reservations"]))
    check("s2 pair get", call("GET", f"/reservations/{combo['reference']}", token=ada).json() == combo)
    slot = slot_at("r_anker", DATE, 2, "20:00")
    check("s2 pair occupies both", slot["available_table_ids"] == ["t_3"] and [o["table_ids"] for o in slot["available_options"]] == [["t_3"]], json.dumps(slot))
    expect("s2 single blocked by pair", book(bob, table="t_2", party=2, at=f"{DATE}T21:00"), 409, "table_unavailable")
    expect("s2 other pair blocked by pair", post(body(table_ids=["t_2", "t_3"], starts_at_local=f"{DATE}T21:00", party_size=8), bob), 409, "table_unavailable")

    # error codes
    expect("s2 both fields", post(body(table_id="t_3", table_ids=["t_3"])), 422, "validation_failed")
    expect("s2 duplicate", post(body(table_ids=["t_3", "t_3"])), 422, "validation_failed")
    expect("s2 empty set", post(body(table_ids=[])), 422, "validation_failed")
    expect("s2 three tables", post(body(table_ids=["t_1", "t_2", "t_3"])), 422, "combination_not_allowed")
    expect("s2 undeclared pair", post(body(table_ids=["t_1", "t_3"])), 422, "combination_not_allowed")
    expect("s2 pair capacity", post(body(table_ids=["t_1", "t_2"], party_size=7)), 422, "party_exceeds_capacity")
    expect("s2 pair unknown table", post(body(table_ids=["t_1", "t_x"])), 404, "not_found")
    expect("s2 table_ids wrong type", post(body(table_ids="t_3")), 400, "malformed_request")
    expect("s2 table_ids element wrong type", post(body(table_ids=[3])), 400, "malformed_request")
    expect("s2 neither field", post(body()), 422, "validation_failed")

    # PATCH and moves with table_ids
    p = expect("s2 patch to pair", call("PATCH", f"/reservations/{single2['reference']}", body={"table_ids": ["t_2", "t_3"], "starts_at_local": f"{DATE}T21:30", "party_size": 9}, token=ada), 200).json()
    check("s2 patch pair shape", p["table_ids"] == ["t_2", "t_3"] and "table_id" not in p, json.dumps(p))
    expect("s2 patch undeclared", call("PATCH", f"/reservations/{single2['reference']}", body={"table_ids": ["t_1", "t_3"]}, token=ada), 422, "combination_not_allowed")
    expect("s2 patch both", call("PATCH", f"/reservations/{single2['reference']}", body={"table_id": "t_3", "table_ids": ["t_3"]}, token=ada), 422, "validation_failed")
    p = expect("s2 patch back to single", call("PATCH", f"/reservations/{single2['reference']}", body={"table_id": "t_3", "party_size": 2}, token=ada), 200).json()
    check("s2 patch single shape", p["table_ids"] == ["t_3"] and p["table_id"] == "t_3")
    mv = expect("s2 move to pair", call("POST", "/reservation-moves", body={"moves": [{"reference": single["reference"], "table_ids": ["t_1", "t_2"], "starts_at_local": f"{DATE}T18:00", "party_size": 6}]}, token=ada, key=new_key()), 201).json()
    check("s2 move shape", mv["reservations"][0]["table_ids"] == ["t_1", "t_2"])
    expect("s2 move batch sharing a table", call("POST", "/reservation-moves", body={"moves": [
        {"reference": single["reference"], "table_ids": ["t_1", "t_2"], "starts_at_local": f"{DATE}T21:30"},
        {"reference": single2["reference"], "table_ids": ["t_2", "t_3"], "starts_at_local": f"{DATE}T21:30"}]}, token=ada, key=new_key()), 409, "table_unavailable")
    expect("s2 cancel pair", call("POST", f"/reservations/{combo['reference']}/cancel", token=ada), 200)
    check("s2 cancel frees both", slot_at("r_anker", DATE, 2, "20:00")["available_table_ids"] == ["t_1", "t_2", "t_3"])

    # seeded reservations with table_ids / status
    pair_seed = {k: v for k, v in seed(DATE, reference="SEEDPAIR", party_size=6).items() if k != "table_id"}
    pair_seed["table_ids"] = ["t_1", "t_2"]
    cxl_seed = {**seed(DATE, reference="SEEDCXL", at="21:30", status="cancelled"), "id": "res_cxl"}
    expect("s2 reset seeded pair", reset(fixture(restaurants=[combo_restaurant()], reservations=[pair_seed, cxl_seed])), 204)
    ada = login(ADA["email"], ADA["password"])
    got = call("GET", "/reservations/SEEDPAIR", token=ada).json()
    check("s2 seeded pair shape", got["table_ids"] == ["t_1", "t_2"] and "table_id" not in got, json.dumps(got))
    check("s2 seeded cancelled free", "t_2" in slot_tables("r_anker", DATE, 2, "21:30"))
    check("s2 seeded pair occupies", slot_tables("r_anker", DATE, 2, "19:00") == ["t_3"])
    expect("s2 seeded both fields", reset(fixture(restaurants=[combo_restaurant()], reservations=[{**seed(DATE), "table_ids": ["t_2"]}])), 422, "validation_failed")

    # export/import round trip keeps table sets and combinable
    reset(fixture(restaurants=[combo_restaurant()]))
    ada = login(ADA["email"], ADA["password"])
    combo = expect("s2 pair before export", post(body(table_ids=["t_1", "t_2"], starts_at_local=f"{DATE}T19:00", party_size=6), ada), 201).json()
    snap = call("GET", "/_test/export").json()
    reset(fixture())
    expect("s2 import", call("POST", "/_test/import", body=snap), 204)
    check("s2 import keeps pair", call("GET", f"/reservations/{combo['reference']}", token=ada).json() == combo)
    check("s2 import keeps combinable", call("GET", "/restaurants/r_anker").json()["combinable"] == [["t_1", "t_2"], ["t_2", "t_3"]])
    # concurrency: 50 different users race for one pair and its members
    users = [dict(ADA, id=f"u_{i}", email=f"r{i}@example.com") for i in range(50)]
    reset(fixture(users=users, restaurants=[combo_restaurant()]))
    tokens = burst(lambda i: login(users[i]["email"], users[i]["password"]), 50)
    out = burst(lambda i: post(body(table_ids=["t_1", "t_2"], starts_at_local=f"{DATE}T19:00", party_size=6) if i % 2 else body(table_id="t_2", starts_at_local=f"{DATE}T19:00", party_size=2), tokens[i]), 50)
    counts = tally(out)
    check("s2 pair/single race: one winner", counts.get(201) == 1 and counts.get(409) == 49, str(counts))


def test_stage1_upgrade(stage1_url):
    """Import an export produced by the stage-1 service (format_version 1, table_id only)."""
    def s1(method, path, body=None, token=None, key=None):
        hdrs = {"Content-Type": "application/json"}
        if token:
            hdrs["Authorization"] = f"Bearer {token}"
        if key:
            hdrs["Idempotency-Key"] = key
        req = urllib.request.Request(stage1_url + path, data=json.dumps(body).encode() if body is not None else None, method=method, headers=hdrs)
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"null")
    assert s1("POST", "/_test/reset", fixture())[0] == 204
    token = s1("POST", "/auth/login", {"email": ADA["email"], "password": ADA["password"]})[1]["token"]
    key = new_key()
    body = {"restaurant_id": "r_anker", "table_id": "t_2", "starts_at_local": f"{DATE}T19:00", "party_size": 4}
    receipt = s1("POST", "/reservations", body, token, key)[1]
    snap = s1("GET", "/_test/export")[1]
    check("upgrade: stage-1 export has no combinable", "combinable" not in snap["state"]["restaurants"][0])
    reset(fixture(restaurants=[restaurant("r_other")]))
    expect("upgrade import", call("POST", "/_test/import", body=snap), 204)
    expect("upgrade token valid", call("GET", "/reservations", token=token), 200)
    got = call("GET", f"/reservations/{receipt['reference']}", token=token).json()
    check("upgrade reservation kept", got["reference"] == receipt["reference"] and got["table_ids"] == ["t_2"] and got["table_id"] == "t_2"
          and got["reservation_id"] == receipt["reservation_id"] and got["created_at"] == receipt["created_at"], json.dumps(got))
    rep = expect("upgrade replay", call("POST", "/reservations", body=body, token=token, key=key), 200).json()
    check("upgrade replay body", rep == receipt)
    check("upgrade combinable empty", call("GET", "/restaurants/r_anker").json()["combinable"] == [])
    expect("upgrade login", call("POST", "/auth/login", body={"email": ADA["email"], "password": ADA["password"]}), 200)
    expect("upgrade import twice", call("POST", "/_test/import", body=snap), 204)
    check("upgrade no duplicates", len(call("GET", "/reservations", token=token).json()["reservations"]) == 1)
    check("upgrade pair bookable after import", "t_2" not in slot_tables("r_anker", DATE, 4, "19:00"))


def main():
    stage1_url = sys.argv[2].rstrip("/") if len(sys.argv) > 2 else None
    test_health_and_routing()
    test_reset_and_auth()
    test_restaurants_and_availability()
    test_reservations()
    test_seeded()
    test_idempotency()
    test_dst()
    test_moves()
    test_export_import()
    test_concurrency()
    test_stage2_api()
    if stage1_url:
        test_stage1_upgrade(stage1_url)
    else:
        print("note: no stage-1 URL given; upgrade import check skipped")
    print(f"\n{PASSES} checks passed, {len(FAILURES)} failed")
    sys.exit(1 if FAILURES else 0)


if __name__ == "__main__":
    main()
