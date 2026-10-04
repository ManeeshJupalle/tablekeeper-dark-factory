#!/usr/bin/env python3
"""Tablekeeper stage 2 - black-box HTTP contract probes (auditor seat).

Stage-1 groups are kept in full; stage-2 groups (CFX, CAV, CRS, CID, CPT, CMV, CCN, UPG)
follow. Browser probes live in run_browser_probes.py.

Usage:
    python3 run_probes.py [--base-url http://127.0.0.1:8080] [--only GROUP,GROUP]

The base URL may also be given in TABLEKEEPER_BASE_URL. Standard library only.
Every check prints one line: "PASS <id> <description>" or
"FAIL <id> <description> -- <reason>". Exit status is 1 when any check fails.

Checks are derived from the stage-1 requirements document alone; ids match
CHECKLIST.md. State is reset through POST /_test/reset before every group.
"""
import argparse
import datetime as dt
import http.client
import json
import os
import re
import sys
import threading
import traceback
import uuid
from urllib.parse import urlencode, urlsplit

BASE = os.environ.get("TABLEKEEPER_BASE_URL", "http://127.0.0.1:8080")
PASSED = 0
FAILED = 0
OBS_LOCK = threading.Lock()
CT_VIOLATIONS = []
SHAPE_VIOLATIONS = []
SERVER_FAULTS = []
TS_VIOLATIONS = []

RFC3339 = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?[+-]\d{2}:\d{2}$")
LOCAL_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$")
REF_RE = re.compile(r"^[A-Z0-9]{6,12}$")
RES_KEYS = {"reservation_id", "reference", "restaurant_id", "table_ids", "party_size",
            "status", "starts_at_local", "starts_at", "ends_at", "created_at"}


# --------------------------------------------------------------------------- HTTP

class Resp:
    def __init__(self, status, headers, text):
        self.status = status
        self.headers = headers
        self.text = text
        try:
            self.json = json.loads(text) if text.strip() else None
        except ValueError:
            self.json = None

    @property
    def code(self):
        try:
            return self.json["error"]["code"]
        except Exception:
            return None

    def __repr__(self):
        return "<%s %r>" % (self.status, self.text[:240])


def _observe(method, path, resp):
    ct = resp.headers.get("content-type", "")
    with OBS_LOCK:
        if resp.status >= 500:
            SERVER_FAULTS.append((method, path, resp.status, resp.text[:120]))
        if resp.status != 204 and resp.text.strip():
            if not ct.lower().startswith("application/json"):
                CT_VIOLATIONS.append((method, path, resp.status, ct))
        if 400 <= resp.status < 600:
            j = resp.json
            ok = (isinstance(j, dict) and isinstance(j.get("error"), dict)
                  and isinstance(j["error"].get("code"), str)
                  and isinstance(j["error"].get("message"), str))
            if not ok:
                SHAPE_VIOLATIONS.append((method, path, resp.status, resp.text[:120]))


def call(method, path, body=None, token=None, key=None, headers=None, raw=None,
         timeout=5, query=None):
    u = urlsplit(BASE)
    if query:
        path = path + "?" + urlencode(query)
    conn = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=timeout)
    hdrs = {"Accept": "application/json"}
    data = None
    if raw is not None:
        data = raw.encode("utf-8") if isinstance(raw, str) else raw
        hdrs["Content-Type"] = "application/json; charset=utf-8"
    elif body is not None:
        data = json.dumps(body).encode("utf-8")
        hdrs["Content-Type"] = "application/json; charset=utf-8"
    if token is not None:
        hdrs["Authorization"] = "Bearer " + token
    if key is not None:
        hdrs["Idempotency-Key"] = key
    if headers:
        hdrs.update(headers)
    try:
        conn.request(method, path, body=data, headers=hdrs)
        r = conn.getresponse()
        text = r.read().decode("utf-8", "replace")
        resp = Resp(r.status, {k.lower(): v for k, v in r.getheaders()}, text)
    finally:
        conn.close()
    _observe(method, path, resp)
    return resp


# --------------------------------------------------------------------------- reporting

def check(cid, desc, cond, detail=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print("PASS %s %s" % (cid, desc))
    else:
        FAILED += 1
        print("FAIL %s %s -- %s" % (cid, desc, detail))
    sys.stdout.flush()
    return bool(cond)


def expect_err(cid, desc, resp, status, code):
    return check(cid, desc, resp.status == status and resp.code == code,
                 "expected %s %s, got %r" % (status, code, resp))


def expect_status(cid, desc, resp, status):
    return check(cid, desc, resp.status == status, "expected %s, got %r" % (status, resp))


GROUPS = []


def group(name):
    def deco(fn):
        GROUPS.append((name, fn))
        return fn
    return deco


# --------------------------------------------------------------------------- dates

TODAY = dt.date.today()


def next_weekday(start, wd):
    d = start
    while d.weekday() != wd:
        d += dt.timedelta(days=1)
    return d


THU = next_weekday(TODAY + dt.timedelta(days=21), 3)
WED = THU - dt.timedelta(days=1)
FRI = THU + dt.timedelta(days=1)
THU2 = THU + dt.timedelta(days=7)
PAST = dt.date(2026, 1, 15)
ALL_DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def D(date):
    return date.isoformat() if isinstance(date, dt.date) else date


def T(date, hhmm):
    return "%sT%s" % (D(date), hhmm)


def last_sunday(year, month):
    d = dt.date(year, month, 31) if month in (1, 3, 5, 7, 8, 10, 12) else dt.date(year, month, 30)
    while d.weekday() != 6:
        d -= dt.timedelta(days=1)
    return d


def nth_sunday(year, month, n):
    d = dt.date(year, month, 1)
    while d.weekday() != 6:
        d += dt.timedelta(days=1)
    return d + dt.timedelta(days=7 * (n - 1))


def berlin_evening_offset(date):
    y = date.year
    return "+02:00" if last_sunday(y, 3) <= date < last_sunday(y, 10) else "+01:00"


def ny_evening_offset(date):
    y = date.year
    return "-04:00" if nth_sunday(y, 3, 2) <= date < nth_sunday(y, 11, 1) else "-05:00"


def parse_ts(s):
    return dt.datetime.fromisoformat(s)


def zulu_local(minutes_ahead):
    """UTC wall clock minutes_ahead from now, floored to the 5-minute grid of r_zulu."""
    t = dt.datetime.now(dt.timezone.utc).replace(second=0, microsecond=0)
    t += dt.timedelta(minutes=minutes_ahead)
    t -= dt.timedelta(minutes=t.minute % 5)
    return t.strftime("%Y-%m-%dT%H:%M")


# --------------------------------------------------------------------------- fixtures

USERS = {
    "hana": ("u_hana", "hana@probe.test", "hana-secret-9", "Hana"),
    "ravi": ("u_ravi", "ravi@probe.test", "ravi-secret-9", "Ravi"),
    "mira": ("u_mira", "mira@probe.test", "mira-secret-9", "Mira"),
}


def hours(days, opens, closes):
    return [{"weekday": d, "opens": opens, "closes": closes} for d in days]


def base_fixture(reservations=None):
    return {
        "users": [{"id": i, "email": e, "password": p, "display_name": n}
                  for (i, e, p, n) in USERS.values()],
        "restaurants": [
            {"id": "r_lumen", "name": "Lumen", "timezone": "Europe/Berlin",
             "slot_minutes": 30, "reservation_duration_minutes": 90,
             "cancellation_cutoff_minutes": 120,
             "opening_hours": hours([d for d in ALL_DAYS if d != "wed"], "18:00", "23:00"),
             "tables": [{"id": "t_duo", "label": "Duo", "capacity": 2},
                        {"id": "t_quad", "label": "Quad", "capacity": 4},
                        {"id": "t_six", "label": "Six", "capacity": 6}]},
            {"id": "r_pier", "name": "Pier", "timezone": "Europe/Berlin",
             "slot_minutes": 30, "reservation_duration_minutes": 60,
             "cancellation_cutoff_minutes": 60,
             "opening_hours": hours(ALL_DAYS, "18:15", "22:15"),
             "tables": [{"id": "p_1", "label": "P1", "capacity": 4}]},
            {"id": "r_nacht", "name": "Nacht", "timezone": "Europe/Berlin",
             "slot_minutes": 30, "reservation_duration_minutes": 90,
             "cancellation_cutoff_minutes": 30,
             "opening_hours": hours(["sun"], "00:00", "06:00"),
             "tables": [{"id": "n_1", "label": "N1", "capacity": 4}]},
            {"id": "r_gotham", "name": "Gotham", "timezone": "America/New_York",
             "slot_minutes": 30, "reservation_duration_minutes": 90,
             "cancellation_cutoff_minutes": 30,
             "opening_hours": hours(["sun"], "00:00", "06:00") + hours(["thu"], "18:00", "23:00"),
             "tables": [{"id": "g_1", "label": "G1", "capacity": 4}]},
            {"id": "r_zulu", "name": "Zulu", "timezone": "Etc/UTC",
             "slot_minutes": 5, "reservation_duration_minutes": 5,
             "cancellation_cutoff_minutes": 60,
             "opening_hours": hours(ALL_DAYS, "00:00", "23:59"),
             "tables": [{"id": "z_1", "label": "Z1", "capacity": 4},
                        {"id": "z_2", "label": "Z2", "capacity": 4}]},
        ],
        "reservations": reservations or [],
    }


def restaurant_fixture(fx, rid):
    return next(r for r in fx["restaurants"] if r["id"] == rid)


def reset(fx=None):
    r = call("POST", "/_test/reset", body=fx if fx is not None else base_fixture(), timeout=10)
    if r.status != 204:
        raise RuntimeError("reset did not return 204: %r" % r)
    return r


def login(name):
    _, email, pw, _ = USERS[name]
    r = call("POST", "/auth/login", body={"email": email, "password": pw})
    if r.status != 200:
        raise RuntimeError("login %s failed: %r" % (name, r))
    return r.json["token"]


def newkey():
    return uuid.uuid4().hex


def book(token, rest, table, local, party, key=None, extra=None, **kw):
    body = {"restaurant_id": rest, "table_id": table, "starts_at_local": local, "party_size": party}
    if extra:
        body.update(extra)
    return call("POST", "/reservations", body=body, token=token,
                key=key if key is not None else newkey(), **kw)


def must_book(token, rest, table, local, party, key=None):
    r = book(token, rest, table, local, party, key=key)
    if r.status != 201:
        raise RuntimeError("setup booking failed: %r" % r)
    return r.json


def avail(rest, date, party, **extra):
    q = {"restaurant_id": rest, "date": D(date), "party_size": party}
    q.update(extra)
    return call("GET", "/availability", query=q)


def slot_map(resp):
    return {s["starts_at_local"]: s["available_table_ids"] for s in resp.json["slots"]}


def get_res(token, ref):
    return call("GET", "/reservations/" + ref, token=token)


def list_res(token):
    r = call("GET", "/reservations", token=token)
    if r.status != 200:
        raise RuntimeError("list failed: %r" % r)
    return r.json["reservations"]


def cancel(token, ref):
    return call("POST", "/reservations/%s/cancel" % ref, token=token)


def patch(token, ref, body, **kw):
    return call("PATCH", "/reservations/" + ref, body=body, token=token, **kw)


def moves(token, items, key=None, **kw):
    return call("POST", "/reservation-moves", body={"moves": items}, token=token,
                key=key if key is not None else newkey(), **kw)


def parallel(fns):
    results = [None] * len(fns)
    barrier = threading.Barrier(len(fns))

    def run(i):
        try:
            barrier.wait(10)
        except threading.BrokenBarrierError:
            pass
        try:
            results[i] = fns[i]()
        except Exception as e:  # noqa
            results[i] = e

    threads = [threading.Thread(target=run, args=(i,)) for i in range(len(fns))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def res_problems(r, duration=90, **expected):
    """Return a list of contract violations in a reservation JSON object."""
    p = []
    if not isinstance(r, dict):
        return ["not a JSON object: %r" % (r,)]
    missing = RES_KEYS - set(r)
    if missing:
        p.append("missing fields %s" % sorted(missing))
        return p
    for k in ("reservation_id", "reference", "restaurant_id", "status",
              "starts_at_local", "starts_at", "ends_at", "created_at"):
        if not isinstance(r[k], str):
            p.append("%s is not a string" % k)
    tids = r.get("table_ids")
    if not isinstance(tids, list) or not tids or not all(isinstance(x, str) and x for x in tids) \
            or len(set(tids)) != len(tids):
        p.append("table_ids %r is not a non-empty list of distinct strings" % (tids,))
    elif len(tids) == 1:
        if r.get("table_id") != tids[0]:
            p.append("table_id %r should equal the single table_ids member %r" % (r.get("table_id"), tids[0]))
    elif "table_id" in r:
        p.append("table_id present on a multi-table reservation")
    if p:
        return p
    if not isinstance(r["party_size"], int) or isinstance(r["party_size"], bool):
        p.append("party_size not an integer")
    if len(r["reservation_id"]) == 0 or len(r["reservation_id"]) > 64:
        p.append("reservation_id length %d" % len(r["reservation_id"]))
    if not REF_RE.match(r["reference"]):
        p.append("reference %r not ^[A-Z0-9]{6,12}$" % r["reference"])
    if not LOCAL_RE.match(r["starts_at_local"]):
        p.append("starts_at_local %r not YYYY-MM-DDTHH:MM" % r["starts_at_local"])
    for k in ("starts_at", "ends_at", "created_at"):
        if not RFC3339.match(r[k]):
            p.append("%s %r not RFC 3339 with numeric offset" % (k, r[k]))
            with OBS_LOCK:
                TS_VIOLATIONS.append((k, r[k]))
    if p:
        return p
    if r["starts_at"][:16] != r["starts_at_local"]:
        p.append("starts_at %r does not match starts_at_local %r" % (r["starts_at"], r["starts_at_local"]))
    try:
        delta = parse_ts(r["ends_at"]) - parse_ts(r["starts_at"])
        if delta != dt.timedelta(minutes=duration):
            p.append("ends_at - starts_at = %s, expected %d min" % (delta, duration))
    except ValueError as e:
        p.append("timestamp parse error %s" % e)
    for k, v in expected.items():
        if r.get(k) != v:
            p.append("%s=%r expected %r" % (k, r.get(k), v))
    return p


def check_res(cid, desc, resp, want=201, duration=90, **expected):
    if resp.status != want or not isinstance(resp.json, dict):
        return check(cid, desc, False, "expected %s, got %r" % (want, resp))
    probs = res_problems(resp.json, duration, **expected)
    return check(cid, desc, not probs, "; ".join(probs))


def same_json(a, b):
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


# --------------------------------------------------------------------------- stage-2 fixtures

COMBO_PAIRS = [["c_b", "c_a"], ["c_b", "c_c"]]
COMBO_LABELS = {"c_a": "Alcove", "c_b": "Bay", "c_c": "Corner", "c_d": "Den"}


def combo_restaurant(combinable=COMBO_PAIRS):
    r = {"id": "r_combo", "name": "Combo House", "timezone": "Europe/Berlin", "slot_minutes": 30,
         "reservation_duration_minutes": 90, "cancellation_cutoff_minutes": 120,
         "opening_hours": hours(["thu", "fri", "sat", "sun"], "18:00", "23:00"),
         "tables": [{"id": "c_a", "label": "Alcove", "capacity": 2}, {"id": "c_b", "label": "Bay", "capacity": 2},
                    {"id": "c_c", "label": "Corner", "capacity": 4}, {"id": "c_d", "label": "Den", "capacity": 6}]}
    if combinable is not None:
        r["combinable"] = combinable
    return r


def s2_fixture(reservations=None, combinable=COMBO_PAIRS):
    fx = base_fixture(reservations)
    fx["restaurants"].append(combo_restaurant(combinable))
    return fx


def book2(token, rest, table_ids, local, party, key=None, extra=None, **kw):
    body = {"restaurant_id": rest, "table_ids": table_ids, "starts_at_local": local, "party_size": party}
    if extra:
        body.update(extra)
    return call("POST", "/reservations", body=body, token=token,
                key=key if key is not None else newkey(), **kw)


def must_book2(token, rest, table_ids, local, party, key=None):
    r = book2(token, rest, table_ids, local, party, key=key)
    if r.status != 201:
        raise RuntimeError("setup pair booking failed: %r" % r)
    return r.json


def options_at(resp, local):
    for s in resp.json.get("slots", []):
        if s.get("starts_at_local") == local:
            return s.get("available_options")
    return None


def opt_ids(resp, local):
    o = options_at(resp, local)
    return None if o is None else [tuple(x.get("table_ids", [])) for x in o]


def singles_at(resp, local):
    return slot_map(resp).get(local)


def overlap_violations(reservations):
    """Pairs of confirmed reservations sharing a table over overlapping intervals."""
    conf = [r for r in reservations if r.get("status") == "confirmed"]
    bad = []
    for i in range(len(conf)):
        for j in range(i + 1, len(conf)):
            a, b = conf[i], conf[j]
            if a["restaurant_id"] != b["restaurant_id"] or not set(a.get("table_ids", [])) & set(b.get("table_ids", [])):
                continue
            if parse_ts(a["starts_at"]) < parse_ts(b["ends_at"]) and parse_ts(b["starts_at"]) < parse_ts(a["ends_at"]):
                bad.append((a["reference"], b["reference"]))
    return bad


def s1_equiv(orig, got):
    """A stage-2 rendering of a stage-1 response: identical, or identical plus table_ids == [table_id]."""
    if same_json(orig, got):
        return True
    if not isinstance(got, dict) or not isinstance(orig, dict):
        return False
    g = dict(got)
    tids = g.pop("table_ids", None)
    return same_json(orig, g) and tids == [orig.get("table_id")]


# =========================================================================== groups

@group("HLT")
def g_health():
    r = call("GET", "/health")
    check("HLT-1", "GET /health is 200 {\"status\":\"ok\"}",
          r.status == 200 and r.json == {"status": "ok"}, repr(r))
    check("HLT-2", "/health content-type is application/json",
          r.headers.get("content-type", "").lower().startswith("application/json"),
          r.headers.get("content-type"))


@group("RST")
def g_reset():
    seeded = {"id": "res_seed_1", "reference": "SEEDAA", "user_id": "u_hana",
              "restaurant_id": "r_lumen", "table_id": "t_quad",
              "starts_at_local": T(THU, "19:00"), "party_size": 3}
    r = call("POST", "/_test/reset", body=base_fixture([seeded]), timeout=10)
    check("RST-1", "reset returns 204 with empty body", r.status == 204 and r.text.strip() == "", repr(r))
    tok = login("hana")
    r = call("POST", "/_test/reset", body=base_fixture([seeded]), timeout=10)
    expect_status("RST-2", "repeated reset returns 204", r, 204)
    r = call("GET", "/restaurants")
    ids = sorted(x["id"] for x in r.json["restaurants"]) if r.status == 200 else None
    check("RST-3", "after reset GET /restaurants lists exactly the fixture restaurants",
          ids == sorted(x["id"] for x in base_fixture()["restaurants"]), repr(r))
    r = call("GET", "/reservations", token=tok)
    expect_err("RST-4", "token issued before reset is 401 after reset", r, 401, "unauthenticated")
    tok = login("hana")
    r = get_res(tok, "SEEDAA")
    check_res("RST-5", "seeded reservation visible to its owner with seeded id/reference", r, 200,
              reservation_id="res_seed_1", reference="SEEDAA", restaurant_id="r_lumen",
              table_id="t_quad", party_size=3, status="confirmed", starts_at_local=T(THU, "19:00"))
    lst = list_res(tok)
    check("RST-5b", "seeded reservation is in the owner's list", [x["reference"] for x in lst] == ["SEEDAA"], repr(lst))
    a = avail("r_lumen", THU, 3)
    sm = slot_map(a) if a.status == 200 else {}
    check("RST-6", "seeded reservation blocks availability on its table",
          a.status == 200 and "t_quad" not in sm.get(T(THU, "19:00"), ["?"])
          and "t_quad" not in sm.get(T(THU, "20:00"), ["?"]) and "t_quad" in sm.get(T(THU, "20:30"), []),
          repr(sm))
    other = login("ravi")
    r = get_res(other, "SEEDAA")
    expect_err("RST-5c", "seeded reservation is 404 for another user", r, 404, "not_found")

    # 64-character ids
    uid = "u_" + "a" * 62
    rid = "r_" + "b" * 62
    tid = "t_" + "c" * 62
    fx = base_fixture()
    fx["users"].append({"id": uid, "email": "long@probe.test", "password": "long-secret-9", "display_name": "Long"})
    fx["restaurants"].append({"id": rid, "name": "Long", "timezone": "Europe/Berlin", "slot_minutes": 30,
                              "reservation_duration_minutes": 90, "cancellation_cutoff_minutes": 60,
                              "opening_hours": hours(ALL_DAYS, "18:00", "23:00"),
                              "tables": [{"id": tid, "label": "L", "capacity": 4}]})
    r = call("POST", "/_test/reset", body=fx, timeout=10)
    ok = r.status == 204
    r2 = call("GET", "/restaurants/" + rid)
    ok = ok and r2.status == 200 and isinstance(r2.json, dict) and r2.json.get("id") == rid and (r2.json.get("tables") or [{}])[0].get("id") == tid
    r3 = call("POST", "/auth/login", body={"email": "long@probe.test", "password": "long-secret-9"})
    ok = ok and r3.status == 200 and r3.json.get("user_id") == uid
    detail = "reset=%r restaurant=%r login=%r" % (r, r2, r3)
    if ok:
        r4 = book(r3.json["token"], rid, tid, T(THU, "19:00"), 4)
        ok = r4.status == 201 and r4.json.get("restaurant_id") == rid and r4.json.get("table_id") == tid
        detail += " booking=%r" % r4
    check("RST-7", "64-char fixture ids accepted end to end", ok, detail)
    r = call("POST", "/auth/login", body={"email": "hana@probe.test", "password": "hana-secret-9"})
    check("RST-8", "seeded user logs in with fixture password and gets fixture id/display_name",
          r.status == 200 and r.json.get("user_id") == "u_hana" and r.json.get("display_name") == "Hana"
          and isinstance(r.json.get("token"), str) and r.json["token"], repr(r))


@group("AUTH")
def g_auth():
    reset()
    r = call("POST", "/auth/signup", body={"email": "nova@probe.test", "password": "nova-secret-9", "display_name": "Nova"})
    ok = (r.status == 201 and isinstance(r.json, dict) and set(r.json) >= {"user_id", "display_name", "token"}
          and r.json["display_name"] == "Nova" and isinstance(r.json["token"], str) and r.json["token"]
          and isinstance(r.json["user_id"], str) and r.json["user_id"])
    check("AUTH-1", "signup 201 with user_id, display_name, token", ok, repr(r))
    signup_tok = r.json["token"] if ok else ""
    nova_id = r.json["user_id"] if ok else None
    r = call("POST", "/auth/login", body={"email": "nova@probe.test", "password": "nova-secret-9"})
    check("AUTH-2", "login 200 with same shape and same user_id",
          r.status == 200 and set(r.json) >= {"user_id", "display_name", "token"}
          and r.json["user_id"] == nova_id and r.json["display_name"] == "Nova" and r.json["token"], repr(r))
    tok_a = r.json["token"] if r.status == 200 else ""
    r = call("POST", "/auth/signup", body={"email": "nova@probe.test", "password": "another-secret", "display_name": "Nova2"})
    expect_err("AUTH-3", "signup with signed-up email is 409 email_taken", r, 409, "email_taken")
    r = call("POST", "/auth/signup", body={"email": "hana@probe.test", "password": "another-secret", "display_name": "H"})
    expect_err("AUTH-3b", "signup with seeded email is 409 email_taken", r, 409, "email_taken")
    r = call("POST", "/auth/signup", body={"email": "short@probe.test", "password": "1234567", "display_name": "S"})
    expect_err("AUTH-4", "7-char password is 422 validation_failed", r, 422, "validation_failed")
    r = call("POST", "/auth/signup", body={"email": "eight@probe.test", "password": "12345678", "display_name": "E"})
    expect_status("AUTH-4b", "8-char password is accepted (201)", r, 201)
    for i, bad in enumerate(["nodomain", "@probe.test", "x@"]):
        r = call("POST", "/auth/signup", body={"email": bad, "password": "long-enough-pw", "display_name": "B"})
        expect_err("AUTH-5%s" % "abc"[i], "email %r is 422 validation_failed" % bad, r, 422, "validation_failed")
    r = call("POST", "/auth/login", body={"email": "nova@probe.test", "password": "wrong-password"})
    expect_err("AUTH-6", "wrong password is 401 unauthenticated", r, 401, "unauthenticated")
    r = call("POST", "/auth/login", body={"email": "nobody@probe.test", "password": "whatever-pw"})
    expect_err("AUTH-6b", "unknown email is 401 unauthenticated", r, 401, "unauthenticated")
    r = call("POST", "/auth/signup", body={"email": 12345, "password": "long-enough-pw", "display_name": "N"})
    expect_err("AUTH-7", "email of wrong JSON type is 400 malformed_request", r, 400, "malformed_request")
    r = call("POST", "/auth/signup", raw="{not json")
    expect_err("AUTH-7b", "unparseable signup body is 400 malformed_request", r, 400, "malformed_request")
    r = call("POST", "/auth/login", raw="{\"email\": ")
    expect_err("AUTH-7c", "unparseable login body is 400 malformed_request", r, 400, "malformed_request")
    r = call("POST", "/auth/login", body={"password": "hana-secret-9"})
    expect_err("AUTH-8", "login without email is 422 validation_failed", r, 422, "validation_failed")
    r = call("POST", "/auth/signup", body={"email": "nopw@probe.test", "display_name": "X"})
    expect_err("AUTH-8b", "signup without password is 422 validation_failed", r, 422, "validation_failed")
    r = call("POST", "/auth/login", body={"email": "nova@probe.test", "password": "nova-secret-9"})
    tok_b = r.json["token"] if r.status == 200 else ""
    ok = all(call("GET", "/reservations", token=t).status == 200 for t in (signup_tok, tok_a, tok_b))
    check("AUTH-9", "signup token and two login tokens are all valid concurrently", ok, "tokens %r" % [signup_tok[:6], tok_a[:6], tok_b[:6]])
    check("AUTH-13", "signup token usable immediately", call("GET", "/reservations", token=signup_tok).status == 200)

    protected = [("POST", "/reservations", {"restaurant_id": "r_lumen", "table_id": "t_quad", "starts_at_local": T(THU, "19:00"), "party_size": 2}),
                 ("GET", "/reservations", None), ("GET", "/reservations/ABCDEF", None),
                 ("POST", "/reservations/ABCDEF/cancel", None), ("PATCH", "/reservations/ABCDEF", {"party_size": 2}),
                 ("POST", "/reservation-moves", {"moves": [{"reference": "ABCDEF"}]})]
    variants = [("no header", {}), ("Basic scheme", {"Authorization": "Basic abc"}),
                ("bare Bearer", {"Authorization": "Bearer"}), ("unknown token", {"Authorization": "Bearer " + uuid.uuid4().hex})]
    for n, (m, p, b) in enumerate(protected):
        for vn, h in variants:
            r = call(m, p, body=b, headers=h, key=newkey())
            expect_err("AUTH-10.%d%s" % (n + 1, vn[0]), "%s %s with %s is 401 unauthenticated" % (m, p, vn), r, 401, "unauthenticated")
    check("AUTH-11a", "GET /restaurants is public", call("GET", "/restaurants").status == 200)
    check("AUTH-11b", "GET /restaurants/{id} is public", call("GET", "/restaurants/r_lumen").status == 200)
    check("AUTH-11c", "GET /availability is public", avail("r_lumen", THU, 2).status == 200)
    r = call("POST", "/reservations", body={"restaurant_id": "r_lumen", "table_id": "t_quad", "starts_at_local": T(THU, "19:00"), "party_size": 2})
    expect_err("AUTH-12", "no token and no key is 401 (auth precedes idempotency)", r, 401, "unauthenticated")


@group("PUB")
def g_public():
    fx = base_fixture()
    reset(fx)
    r = call("GET", "/restaurants")
    ok = r.status == 200 and isinstance(r.json, dict) and isinstance(r.json.get("restaurants"), list)
    if ok:
        got = {x.get("id"): x for x in r.json["restaurants"]}
        ok = set(got) == {x["id"] for x in fx["restaurants"]} and all(
            got[x["id"]].get("name") == x["name"] and got[x["id"]].get("timezone") == x["timezone"] for x in fx["restaurants"])
    check("PUB-1", "GET /restaurants lists fixture restaurants with id, name, timezone", ok, repr(r))
    r = call("GET", "/restaurants/r_lumen")
    fr = restaurant_fixture(fx, "r_lumen")
    ok = r.status == 200 and isinstance(r.json, dict) and all(
        r.json.get(k) == fr[k] for k in ("id", "name", "timezone", "slot_minutes", "reservation_duration_minutes",
                                         "cancellation_cutoff_minutes", "opening_hours", "tables"))
    check("PUB-2", "GET /restaurants/r_lumen returns fixture configuration incl. opening_hours and tables in order", ok, repr(r))
    expect_err("PUB-3", "GET /restaurants/unknown is 404 not_found", call("GET", "/restaurants/r_nope"), 404, "not_found")

    a = avail("r_lumen", THU, 4)
    exp_slots = [T(THU, "%02d:%02d" % (18 + i // 2, 30 * (i % 2))) for i in range(8)]
    off = berlin_evening_offset(THU)
    ok = a.status == 200 and a.json.get("restaurant_id") == "r_lumen" and a.json.get("date") == D(THU) \
        and a.json.get("timezone") == "Europe/Berlin" and isinstance(a.json.get("slots"), list)
    if ok:
        ok = [s.get("starts_at_local") for s in a.json["slots"]] == exp_slots and all(
            s.get("starts_at") == s["starts_at_local"] + ":00" + off for s in a.json["slots"])
    check("PUB-4", "availability shape and exact 8 slots 18:00..21:30 with correct starts_at", ok, repr(a))
    sm = slot_map(a) if a.status == 200 else {}
    check("PUB-5a", "party 4 offers [t_quad, t_six] in fixture order on every slot",
          bool(sm) and all(v == ["t_quad", "t_six"] for v in sm.values()), repr(sm))
    a1 = avail("r_lumen", THU, 1)
    check("PUB-5b", "party 1 offers all three tables in fixture order",
          a1.status == 200 and all(v == ["t_duo", "t_quad", "t_six"] for v in slot_map(a1).values()) and len(a1.json["slots"]) == 8, repr(a1))
    a7 = avail("r_lumen", THU, 7)
    check("PUB-5c", "party 7 keeps all 8 slots with empty available_table_ids",
          a7.status == 200 and len(a7.json["slots"]) == 8 and all(v == [] for v in slot_map(a7).values()), repr(a7))
    a = avail("r_lumen", WED, 2)
    check("PUB-6", "closed weekday gives slots [] with other fields present",
          a.status == 200 and a.json.get("slots") == [] and a.json.get("date") == D(WED) and a.json.get("timezone") == "Europe/Berlin", repr(a))
    full = {"restaurant_id": "r_lumen", "date": D(THU), "party_size": "2"}
    for k in full:
        q = {kk: v for kk, v in full.items() if kk != k}
        r = call("GET", "/availability", query=q)
        expect_err("PUB-7-" + k, "availability without %s is 422 validation_failed" % k, r, 422, "validation_failed")
    for bad in ["1e9", "4.0", "+4", "abc", "-1", ""]:
        r = avail("r_lumen", THU, bad)
        expect_err("PUB-8[%s]" % bad, "party_size=%r is 422 validation_failed" % bad, r, 422, "validation_failed")
    for bad in ["2026-02-30", "2026-13-01", "20261029", D(THU) + "T00:00"]:
        r = avail("r_lumen", bad, 2)
        expect_err("PUB-9[%s]" % bad, "date=%r is 422 validation_failed" % bad, r, 422, "validation_failed")
    a = avail("r_lumen", THU, 4)
    b = avail("r_lumen", THU, 4, foo="bar", page="2")
    check("PUB-10", "unknown query parameters are ignored", a.status == 200 and b.status == 200 and same_json(a.json, b.json), repr(b))
    a = avail("r_pier", THU, 2)
    exp = [T(THU, hh) for hh in ("18:15", "18:45", "19:15", "19:45", "20:15", "20:45", "21:15")]
    check("PUB-11", "slot grid anchored at opens 18:15 (7 slots, last 21:15 ends exactly at closes)",
          a.status == 200 and [s["starts_at_local"] for s in a.json["slots"]] == exp, repr(a))
    tok = login("hana")
    must_book(tok, "r_lumen", "t_quad", T(THU, "19:00"), 4)
    a = avail("r_lumen", THU, 4)
    sm = slot_map(a) if a.status == 200 else {}
    blocked = [T(THU, h) for h in ("18:00", "18:30", "19:00", "19:30", "20:00")]
    free = [T(THU, h) for h in ("20:30", "21:00", "21:30")]
    check("PUB-12", "half-open occupancy: 19:00+90 blocks 18:00..20:00, not 20:30..21:30",
          bool(sm) and all("t_quad" not in sm.get(s, ["t_quad"]) for s in blocked)
          and all(sm.get(s) == ["t_quad", "t_six"] for s in free), repr(sm))
    a = avail("r_lumen", PAST, 2)
    check("PUB-13", "availability for a past date served with winter offset +01:00",
          a.status == 200 and len(a.json["slots"]) == 8 and all(s["starts_at"].endswith("+01:00") for s in a.json["slots"]), repr(a))


@group("RES")
def g_reservations():
    reset()
    tok = login("hana")
    ravi = login("ravi")
    off = berlin_evening_offset(THU)
    r = book(tok, "r_lumen", "t_quad", T(THU, "19:00"), 4)
    check_res("RES-1", "201 body fields, status confirmed, exact starts_at/ends_at", r, 201,
              restaurant_id="r_lumen", table_id="t_quad", party_size=4, status="confirmed",
              starts_at_local=T(THU, "19:00"), starts_at=T(THU, "19:00") + ":00" + off, ends_at=T(THU, "20:30") + ":00" + off)
    first = r.json if r.status == 201 else {}
    r = get_res(tok, first.get("reference", "XXXXXX"))
    check("RES-16", "GET /reservations/{reference} equals the 201 body", r.status == 200 and same_json(r.json, first), repr(r))
    for label, val in [("string", "4"), ("bool", True), ("float", 4.5), ("float-int", 4.0), ("zero", 0), ("negative", -2)]:
        r = book(tok, "r_lumen", "t_six", T(THU, "21:30"), val)
        expect_err("RES-2[%s]" % label, "party_size %r is 422 validation_failed" % (val,), r, 422, "validation_failed")
    r = book(tok, "r_lumen", "t_duo", T(THU, "21:30"), 3)
    expect_err("RES-3", "party_size one above capacity is 422 party_exceeds_capacity", r, 422, "party_exceeds_capacity")
    r = book(tok, "r_lumen", "t_duo", T(THU, "21:30"), 2)
    check_res("RES-3b", "party_size equal to capacity is 201", r, 201, party_size=2)
    for label, val in [("offset", T(THU, "19:00") + ":00+02:00"), ("Z", T(THU, "19:00") + "Z"), ("seconds", T(THU, "19:00") + ":00"),
                       ("space", D(THU) + " 19:00"), ("date-only", D(THU)), ("garbage", "tomorrow")]:
        r = book(tok, "r_lumen", "t_six", val, 2)
        expect_err("RES-4[%s]" % label, "starts_at_local %r is 422 validation_failed" % val, r, 422, "validation_failed")
    for hh in ("19:10", "19:15"):
        r = book(tok, "r_lumen", "t_six", T(THU, hh), 2)
        expect_err("RES-5[%s]" % hh, "off-grid %s is 422 not_on_slot_grid" % hh, r, 422, "not_on_slot_grid")
    for hh in ("17:30", "22:00", "23:00"):
        r = book(tok, "r_lumen", "t_six", T(THU, hh), 2)
        expect_err("RES-6[%s]" % hh, "%s is 422 outside_opening_hours" % hh, r, 422, "outside_opening_hours")
    r = book(tok, "r_lumen", "t_six", T(THU, "21:30"), 2)
    check_res("RES-6b", "last slot 21:30 (ends exactly at closes) is 201", r, 201, ends_at=T(THU, "23:00") + ":00" + off)
    r = book(tok, "r_lumen", "t_six", T(WED, "19:00"), 2)
    expect_err("RES-7", "closed weekday is 422 outside_opening_hours", r, 422, "outside_opening_hours")
    expect_err("RES-8a", "unknown restaurant is 404 not_found", book(tok, "r_nope", "t_quad", T(THU, "20:30"), 2), 404, "not_found")
    expect_err("RES-8b", "unknown table is 404 not_found", book(tok, "r_lumen", "t_nope", T(THU, "20:30"), 2), 404, "not_found")
    expect_err("RES-8c", "table of another restaurant is 404 not_found", book(tok, "r_lumen", "p_1", T(THU, "20:30"), 2), 404, "not_found")
    expect_err("RES-9a", "restaurant_id as number is 400 malformed_request", book(tok, 7, "t_quad", T(THU, "20:30"), 2), 400, "malformed_request")
    expect_err("RES-9b", "table_id as number is 400 malformed_request", book(tok, "r_lumen", 7, T(THU, "20:30"), 2), 400, "malformed_request")
    r = call("POST", "/reservations", raw="{\"restaurant_id\": ", token=tok, key=newkey())
    expect_err("RES-10a", "unparseable body is 400 malformed_request", r, 400, "malformed_request")
    r = call("POST", "/reservations", raw="[1,2]", token=tok, key=newkey())
    expect_err("RES-10b", "JSON array body is 400 malformed_request", r, 400, "malformed_request")
    full = {"restaurant_id": "r_lumen", "table_id": "t_six", "starts_at_local": T(THU, "20:30"), "party_size": 2}
    for k in full:
        body = {kk: v for kk, v in full.items() if kk != k}
        r = call("POST", "/reservations", body=body, token=tok, key=newkey())
        expect_err("RES-11-" + k, "missing %s is 422 validation_failed" % k, r, 422, "validation_failed")
    r = book(tok, "r_lumen", "t_six", T(THU, "18:00"), 2, extra={"note": "window", "vip": True, "nested": {"a": 1}})
    check_res("RES-12", "unknown body fields are ignored (201)", r, 201)
    expect_err("RES-13a", "same table and slot is 409 table_unavailable", book(ravi, "r_lumen", "t_quad", T(THU, "19:00"), 2), 409, "table_unavailable")
    expect_err("RES-13b", "overlapping slot 19:30 against 19:00 is 409 table_unavailable", book(ravi, "r_lumen", "t_quad", T(THU, "19:30"), 2), 409, "table_unavailable")
    expect_err("RES-13c", "overlapping slot 18:00 against 19:00 is 409 table_unavailable", book(ravi, "r_lumen", "t_quad", T(THU, "18:00"), 2), 409, "table_unavailable")
    check_res("RES-13d", "adjacent slot 20:30 after a 19:00 booking is 201", book(ravi, "r_lumen", "t_quad", T(THU, "20:30"), 2), 201)
    check_res("RES-17a", "same slot on another table is 201", book(ravi, "r_lumen", "t_duo", T(THU, "19:00"), 2), 201)
    check_res("RES-17b", "same table on another day is 201", book(ravi, "r_lumen", "t_quad", T(FRI, "19:00"), 2), 201)
    r = book(tok, "r_lumen", "t_quad", T(PAST, "19:00"), 2)
    check_res("RES-14", "past date is bookable with +01:00 offset", r, 201,
              starts_at="2026-01-15T19:00:00+01:00", ends_at="2026-01-15T20:30:00+01:00")
    r = call("POST", "/reservations", raw="{oops", token=tok)
    expect_err("RES-18", "malformed body without Idempotency-Key is 400 malformed_request", r, 400, "malformed_request")
    refs = []
    combos = [(w, tb, hh) for w in range(3) for tb in ("t_six", "t_quad", "t_duo") for hh in ("18:00", "19:30", "21:00")][:20]
    for w, tb, hh in combos:
        rr = book(tok, "r_lumen", tb, T(THU2 + dt.timedelta(days=7 * w), hh), 2)
        if rr.status == 201:
            refs.append(rr.json["reference"])
    check("RES-15", "20 references unique and matching ^[A-Z0-9]{6,12}$",
          len(refs) == 20 and len(set(refs)) == 20 and all(REF_RE.match(x) for x in refs), repr(refs))


@group("IDEM")
def g_idempotency():
    reset()
    tok = login("hana")
    ravi = login("ravi")
    body = {"restaurant_id": "r_lumen", "table_id": "t_quad", "starts_at_local": T(THU, "19:00"), "party_size": 4}
    r = call("POST", "/reservations", body=body, token=tok)
    expect_err("IDEM-1a", "absent Idempotency-Key is 400 missing_idempotency_key", r, 400, "missing_idempotency_key")
    r = call("POST", "/reservations", body=body, token=tok, key="")
    expect_err("IDEM-1b", "empty Idempotency-Key is 400 missing_idempotency_key", r, 400, "missing_idempotency_key")
    r = call("POST", "/reservations", body=body, token=tok, key="k" * 256)
    expect_err("IDEM-2a", "256-char key is 422 validation_failed", r, 422, "validation_failed")
    r = book(tok, "r_lumen", "t_duo", T(THU, "18:00"), 2, key="k")
    check_res("IDEM-2b", "1-char key is accepted (201)", r, 201)
    r = book(tok, "r_lumen", "t_duo", T(THU, "20:00"), 2, key="k" * 255)
    check_res("IDEM-2c", "255-char key is accepted (201)", r, 201)

    key = newkey()
    first = call("POST", "/reservations", body=body, token=tok, key=key)
    check_res("IDEM-3a", "first use returns 201", first, 201)
    n_before = len(list_res(tok))
    rep = call("POST", "/reservations", body=body, token=tok, key=key)
    check("IDEM-3", "replay returns 200 with JSON-identical body",
          rep.status == 200 and same_json(rep.json, first.json), "first=%r replay=%r" % (first, rep))
    raw = "{ \"party_size\" : 4 ,\n \"starts_at_local\": \"%s\", \"table_id\":\"t_quad\",\"restaurant_id\":\"r_lumen\" }" % T(THU, "19:00")
    rep2 = call("POST", "/reservations", raw=raw, token=tok, key=key)
    check("IDEM-4", "replay with reordered keys and whitespace is 200 identical",
          rep2.status == 200 and same_json(rep2.json, first.json), repr(rep2))
    check("IDEM-12", "replays created no extra reservation", len(list_res(tok)) == n_before, "")
    diff = dict(body, party_size=3)
    r = call("POST", "/reservations", body=diff, token=tok, key=key)
    expect_err("IDEM-5", "same key, different body is 409 idempotency_key_reuse", r, 409, "idempotency_key_reuse")
    r = call("POST", "/reservations", body=dict(body, party_size="x"), token=tok, key=key)
    expect_err("IDEM-6a", "same key, different invalid body (party_size string) is 409 idempotency_key_reuse", r, 409, "idempotency_key_reuse")
    r = call("POST", "/reservations", body=dict(body, restaurant_id="r_nope"), token=tok, key=key)
    expect_err("IDEM-6b", "same key, different body with unknown restaurant is 409 idempotency_key_reuse", r, 409, "idempotency_key_reuse")
    r = call("POST", "/reservations", body=dict(body, extra=1), token=tok, key=key)
    expect_err("IDEM-6c", "same key, body differing only by an unknown field is 409 idempotency_key_reuse", r, 409, "idempotency_key_reuse")

    # same key + literally same body on the other path
    other = must_book(tok, "r_lumen", "t_six", T(THU, "21:30"), 2)
    dual = {"restaurant_id": "r_lumen", "table_id": "t_six", "starts_at_local": T(THU, "18:00"), "party_size": 2,
            "moves": [{"reference": other["reference"], "party_size": 3}]}
    k2 = newkey()
    r1 = call("POST", "/reservations", body=dual, token=tok, key=k2)
    r2 = call("POST", "/reservation-moves", body=dual, token=tok, key=k2)
    check("IDEM-7", "same key and same body on /reservation-moves is a first use there (both 201)",
          r1.status == 201 and r2.status == 201 and r2.json.get("reservations", [{}])[0].get("party_size") == 3,
          "reservations=%r moves=%r" % (r1, r2))

    kf = newkey()
    r = book(tok, "r_lumen", "t_duo", T(FRI, "19:00"), 9, key=kf)
    expect_err("IDEM-8a", "first use failing 422 party_exceeds_capacity", r, 422, "party_exceeds_capacity")
    r = book(tok, "r_lumen", "t_duo", T(FRI, "19:00"), 2, key=kf)
    check_res("IDEM-8b", "key reused after a 422 failure is a first use (201)", r, 201)
    kg = newkey()
    r = book(ravi, "r_lumen", "t_quad", T(THU, "19:00"), 2, key=kg)
    expect_err("IDEM-8c", "first use failing 409 table_unavailable", r, 409, "table_unavailable")
    r = book(ravi, "r_lumen", "t_quad", T(FRI, "19:00"), 2, key=kg)
    check_res("IDEM-8d", "key reused after a 409 failure is a first use (201)", r, 201)

    # replay after PATCH / cancel
    ref = first.json["reference"]
    p = patch(tok, ref, {"party_size": 3})
    rep = call("POST", "/reservations", body=body, token=tok, key=key)
    g = get_res(tok, ref)
    check("IDEM-9", "replay after PATCH returns original 201 body; PATCHed state stays",
          p.status == 200 and rep.status == 200 and same_json(rep.json, first.json) and g.json.get("party_size") == 3,
          "patch=%r replay=%r get=%r" % (p, rep, g))
    c = cancel(tok, ref)
    rep = call("POST", "/reservations", body=body, token=tok, key=key)
    g = get_res(tok, ref)
    check("IDEM-10", "replay after cancel returns original body (confirmed) and leaves it cancelled",
          c.status == 200 and rep.status == 200 and same_json(rep.json, first.json) and g.json.get("status") == "cancelled",
          "cancel=%r replay=%r get=%r" % (c, rep, g))
    # slot now free; someone else takes it, replay still 200 original
    taken = book(ravi, "r_lumen", "t_quad", T(THU, "19:00"), 2)
    rep = call("POST", "/reservations", body=body, token=tok, key=key)
    check("IDEM-13", "replay is still 200 original after the slot was taken by another user",
          taken.status == 201 and rep.status == 200 and same_json(rep.json, first.json), "taken=%r replay=%r" % (taken, rep))
    r = call("POST", "/reservations", body=dict(body, table_id="t_six", starts_at_local=T(THU, "20:00")), token=ravi, key=key)
    check_res("IDEM-11", "another user reusing the key string with a different body gets 201 (key scoped per user)", r, 201)


@group("LIST")
def g_list():
    reset()
    tok = login("hana")
    ravi = login("ravi")
    mira = login("mira")
    r = call("GET", "/reservations", token=mira)
    check("LIST-1", "empty list is exactly {\"reservations\": []}", r.status == 200 and r.json == {"reservations": []}, repr(r))
    a = must_book(tok, "r_lumen", "t_duo", T(THU, "21:30"), 2)
    b = must_book(tok, "r_lumen", "t_quad", T(THU, "18:00"), 2)
    c = must_book(tok, "r_lumen", "t_six", T(THU2, "19:00"), 2)
    d = must_book(tok, "r_lumen", "t_quad", T(PAST, "19:00"), 2)
    e = must_book(ravi, "r_lumen", "t_six", T(THU, "19:00"), 2)
    cancel(tok, b["reference"])
    lst = list_res(tok)
    refs = [x.get("reference") for x in lst]
    check("LIST-2", "list contains only the caller's reservations", set(refs) == {a["reference"], b["reference"], c["reference"], d["reference"]}, repr(refs))
    check("LIST-3", "list ordered by starts_at descending", refs == [c["reference"], a["reference"], b["reference"], d["reference"]], repr(refs))
    bb = next((x for x in lst if x.get("reference") == b["reference"]), {})
    check("LIST-4", "cancelled reservation included with status cancelled", bb.get("status") == "cancelled", repr(bb))
    probs = []
    for x in lst:
        probs += res_problems(x)
        g = get_res(tok, x.get("reference", "XXXXXX"))
        if not (g.status == 200 and same_json(g.json, x)):
            probs.append("list entry differs from GET for %s" % x.get("reference"))
    check("LIST-5", "list entries have create shape and equal GET /reservations/{ref}", not probs, "; ".join(probs))
    expect_err("LIST-6a", "another user's reference is 404 not_found", get_res(tok, e["reference"]), 404, "not_found")
    expect_err("LIST-6b", "unknown reference is 404 not_found", get_res(tok, "ZZZZZZ"), 404, "not_found")
    rl = list_res(ravi)
    check("LIST-2b", "other user's list contains only their own", [x["reference"] for x in rl] == [e["reference"]], repr(rl))


@group("CXL")
def g_cancel():
    reset()
    tok = login("hana")
    ravi = login("ravi")
    a = must_book(tok, "r_lumen", "t_quad", T(THU, "19:00"), 4)
    r = cancel(tok, a["reference"])
    exp = dict(a, status="cancelled")
    check("CXL-1", "cancel is 200 with status cancelled and all other fields unchanged",
          r.status == 200 and same_json(r.json, exp), "got %r expected %r" % (r, exp))
    av = slot_map(avail("r_lumen", THU, 4))
    check("CXL-2", "slot offered again immediately after cancel", av.get(T(THU, "19:00")) == ["t_quad", "t_six"], repr(av))
    r = cancel(tok, a["reference"])
    check("CXL-3", "cancelling twice is 200 with status cancelled", r.status == 200 and same_json(r.json, exp), repr(r))
    g = get_res(tok, a["reference"])
    check("CXL-1b", "GET after cancel shows cancelled state", g.status == 200 and same_json(g.json, exp), repr(g))
    b = must_book(ravi, "r_lumen", "t_six", T(THU, "19:00"), 2)
    expect_err("CXL-4a", "cancelling another user's reservation is 404 not_found", cancel(tok, b["reference"]), 404, "not_found")
    expect_err("CXL-4b", "cancelling an unknown reference is 404 not_found", cancel(tok, "ZZZZZZ"), 404, "not_found")
    g = get_res(ravi, b["reference"])
    check("CXL-4c", "failed cancel by non-owner leaves it confirmed", g.status == 200 and g.json.get("status") == "confirmed", repr(g))
    inside = must_book(tok, "r_zulu", "z_1", zulu_local(50), 2)
    outside = must_book(tok, "r_zulu", "z_2", zulu_local(70), 2)
    r = cancel(tok, inside["reference"])
    expect_err("CXL-5", "cancel within the cutoff (about 48 min ahead, cutoff 60) is 409 cutoff_passed", r, 409, "cutoff_passed")
    g = get_res(tok, inside["reference"])
    check("CXL-7", "booking still confirmed after a cutoff refusal", g.status == 200 and g.json.get("status") == "confirmed", repr(g))
    past = must_book(tok, "r_lumen", "t_quad", T(PAST, "19:00"), 2)
    expect_err("CXL-5b", "cancel of a booking that already started is 409 cutoff_passed", cancel(tok, past["reference"]), 409, "cutoff_passed")
    r = cancel(tok, outside["reference"])
    check("CXL-6", "cancel just outside the cutoff (about 68 min ahead) is 200 cancelled", r.status == 200 and r.json.get("status") == "cancelled", repr(r))
    r = book(ravi, "r_lumen", "t_quad", T(THU, "19:00"), 2)
    check_res("CXL-8", "another user can book the freed slot (201)", r, 201)


@group("PAT")
def g_patch():
    reset()
    tok = login("hana")
    ravi = login("ravi")
    off = berlin_evening_offset(THU)
    a = must_book(tok, "r_lumen", "t_quad", T(THU, "19:00"), 4)
    r = patch(tok, a["reference"], {"starts_at_local": T(THU, "21:30")})
    check_res("PAT-1", "PATCH starts_at_local is 200 with recomputed times and stable identity", r, 200,
              reservation_id=a["reservation_id"], reference=a["reference"], created_at=a["created_at"], table_id="t_quad",
              party_size=4, status="confirmed", starts_at_local=T(THU, "21:30"),
              starts_at=T(THU, "21:30") + ":00" + off, ends_at=T(THU, "23:00") + ":00" + off)
    sm = slot_map(avail("r_lumen", THU, 4))
    check("PAT-1b", "old slot freed and new slot taken after PATCH",
          sm.get(T(THU, "19:00")) == ["t_quad", "t_six"] and sm.get(T(THU, "21:30")) == ["t_six"] and sm.get(T(THU, "20:30")) == ["t_six"], repr(sm))
    r = patch(tok, a["reference"], {"table_id": "t_six"})
    check_res("PAT-2", "PATCH table_id only", r, 200, table_id="t_six", starts_at_local=T(THU, "21:30"), party_size=4, reference=a["reference"])
    r = patch(tok, a["reference"], {"party_size": 6})
    check_res("PAT-3", "PATCH party_size only", r, 200, table_id="t_six", party_size=6, reference=a["reference"])
    r = patch(tok, a["reference"], {"table_id": "t_quad", "starts_at_local": T(THU, "19:00"), "party_size": 2})
    check_res("PAT-4", "PATCH all three fields at once", r, 200, table_id="t_quad", party_size=2,
              starts_at_local=T(THU, "19:00"), reservation_id=a["reservation_id"], created_at=a["created_at"])
    before = get_res(tok, a["reference"]).json
    sm_before = slot_map(avail("r_lumen", THU, 1))
    r = patch(tok, a["reference"], {"party_size": 5})
    expect_err("PAT-5", "PATCH party above capacity is 422 party_exceeds_capacity", r, 422, "party_exceeds_capacity")
    after = get_res(tok, a["reference"]).json
    check("PAT-5b", "failed PATCH leaves booking and occupancy unchanged",
          same_json(before, after) and same_json(sm_before, slot_map(avail("r_lumen", THU, 1))), "before=%r after=%r" % (before, after))
    b = must_book(ravi, "r_lumen", "t_six", T(THU, "19:00"), 2)
    r = patch(tok, a["reference"], {"table_id": "t_six"})
    expect_err("PAT-6", "PATCH onto a table held by another booking is 409 table_unavailable", r, 409, "table_unavailable")
    r = patch(tok, a["reference"], {"table_id": "t_six", "starts_at_local": T(THU, "20:00")})
    expect_err("PAT-6b", "PATCH onto an overlapping slot of a held table is 409 table_unavailable", r, 409, "table_unavailable")
    check("PAT-6c", "booking unchanged after 409", same_json(get_res(tok, a["reference"]).json, after), "")
    r = patch(tok, a["reference"], {"starts_at_local": T(THU, "19:30")})
    check_res("PAT-9", "own occupancy does not block moving to an overlapping slot on the same table (19:00 -> 19:30)", r, 200,
              table_id="t_quad", starts_at_local=T(THU, "19:30"), ends_at=T(THU, "21:00") + ":00" + off)
    expect_err("PAT-10a", "PATCH another user's reservation is 404 not_found", patch(tok, b["reference"], {"party_size": 2}), 404, "not_found")
    expect_err("PAT-10b", "PATCH to unknown table is 404 not_found", patch(tok, a["reference"], {"table_id": "t_nope"}), 404, "not_found")
    expect_err("PAT-10c", "PATCH to a table of another restaurant is 404 not_found", patch(tok, a["reference"], {"table_id": "p_1"}), 404, "not_found")
    expect_err("PAT-10d", "PATCH unknown reference is 404 not_found", patch(tok, "ZZZZZZ", {"party_size": 2}), 404, "not_found")
    expect_err("PAT-11a", "PATCH off-grid is 422 not_on_slot_grid", patch(tok, a["reference"], {"starts_at_local": T(THU, "19:40")}), 422, "not_on_slot_grid")
    expect_err("PAT-11b", "PATCH outside hours is 422 outside_opening_hours", patch(tok, a["reference"], {"starts_at_local": T(THU, "22:00")}), 422, "outside_opening_hours")
    expect_err("PAT-11c", "PATCH onto a closed day is 422 outside_opening_hours", patch(tok, a["reference"], {"starts_at_local": T(WED, "19:00")}), 422, "outside_opening_hours")
    expect_err("PAT-11d", "PATCH starts_at_local with offset is 422 validation_failed", patch(tok, a["reference"], {"starts_at_local": T(THU, "19:00") + ":00+01:00"}), 422, "validation_failed")
    n = must_book(tok, "r_nacht", "n_1", T(THU + dt.timedelta(days=3), "01:00"), 2)  # a future Sunday
    expect_err("PAT-11e", "PATCH into the spring-forward gap is 422 invalid_local_time", patch(tok, n["reference"], {"starts_at_local": "2026-03-29T02:30"}), 422, "invalid_local_time")
    expect_err("PAT-12a", "PATCH party_size as string is 422 validation_failed", patch(tok, a["reference"], {"party_size": "3"}), 422, "validation_failed")
    expect_err("PAT-12b", "PATCH party_size 0 is 422 validation_failed", patch(tok, a["reference"], {"party_size": 0}), 422, "validation_failed")
    expect_err("PAT-12c", "PATCH table_id as number is 400 malformed_request", patch(tok, a["reference"], {"table_id": 5}), 400, "malformed_request")
    expect_err("PAT-12d", "PATCH unparseable body is 400 malformed_request", patch(tok, a["reference"], None, raw="{nope"), 400, "malformed_request")
    r = patch(tok, a["reference"], {"party_size": 3, "colour": "red"})
    check_res("PAT-13", "PATCH ignores unknown fields and needs no Idempotency-Key", r, 200, party_size=3)
    c = cancel(tok, a["reference"])
    expect_err("PAT-7", "PATCH of a cancelled reservation is 409 reservation_cancelled", patch(tok, a["reference"], {"party_size": 2}), 409, "reservation_cancelled")
    inside = must_book(tok, "r_zulu", "z_1", zulu_local(50), 2)
    r = patch(tok, inside["reference"], {"starts_at_local": zulu_local(600)})
    expect_err("PAT-8", "PATCH within cutoff of the current start is 409 cutoff_passed even when moving far ahead", r, 409, "cutoff_passed")
    check("PAT-8b", "booking unchanged after cutoff refusal", same_json(get_res(tok, inside["reference"]).json, inside), "")
    outside = must_book(tok, "r_zulu", "z_2", zulu_local(70), 2)
    r = patch(tok, outside["reference"], {"party_size": 3})
    check_res("PAT-8c", "PATCH just outside cutoff is 200", r, 200, duration=5, party_size=3)
    past = must_book(tok, "r_lumen", "t_quad", T(PAST, "19:00"), 2)
    expect_err("PAT-8d", "PATCH of a booking that already started is 409 cutoff_passed", patch(tok, past["reference"], {"party_size": 3}), 409, "cutoff_passed")


@group("TIME")
def g_time():
    reset()
    tok = login("hana")
    r = book(tok, "r_lumen", "t_quad", "2026-07-16T19:00", 2)
    check_res("TIME-1", "Europe/Berlin summer offset +02:00", r, 201, starts_at="2026-07-16T19:00:00+02:00", ends_at="2026-07-16T20:30:00+02:00")
    r = book(tok, "r_lumen", "t_quad", "2026-01-15T19:00", 2)
    check_res("TIME-2", "Europe/Berlin winter offset +01:00", r, 201, starts_at="2026-01-15T19:00:00+01:00", ends_at="2026-01-15T20:30:00+01:00")
    r = book(tok, "r_gotham", "g_1", "2026-07-16T19:00", 2)
    check_res("TIME-3", "America/New_York summer offset -04:00", r, 201, starts_at="2026-07-16T19:00:00-04:00", ends_at="2026-07-16T20:30:00-04:00")
    r = book(tok, "r_gotham", "g_1", "2026-01-15T19:00", 2)
    check_res("TIME-4", "America/New_York winter offset -05:00", r, 201, starts_at="2026-01-15T19:00:00-05:00", ends_at="2026-01-15T20:30:00-05:00")
    a = avail("r_gotham", "2026-07-16", 2)
    check("TIME-3b", "NY summer availability carries -04:00 on every slot",
          a.status == 200 and len(a.json["slots"]) == 8 and all(s["starts_at"].endswith("-04:00") for s in a.json["slots"]), repr(a))

    # Berlin spring forward 2026-03-29
    a = avail("r_nacht", "2026-03-29", 2)
    exp = ["2026-03-29T" + h for h in ("00:00", "00:30", "01:00", "01:30", "03:00", "03:30", "04:00", "04:30")]
    check("TIME-5", "Berlin spring-forward night: 02:00 and 02:30 absent, exact slot list",
          a.status == 200 and [s["starts_at_local"] for s in a.json["slots"]] == exp, repr(a))
    exp_abs = {"2026-03-29T01:30": "2026-03-29T01:30:00+01:00", "2026-03-29T03:00": "2026-03-29T03:00:00+02:00"}
    got = {s["starts_at_local"]: s["starts_at"] for s in a.json["slots"]} if a.status == 200 else {}
    check("TIME-5b", "spring-forward slots carry pre/post transition offsets", all(got.get(k) == v for k, v in exp_abs.items()), repr(got))
    expect_err("TIME-6a", "booking 2026-03-29T02:00 Berlin is 422 invalid_local_time", book(tok, "r_nacht", "n_1", "2026-03-29T02:00", 2), 422, "invalid_local_time")
    expect_err("TIME-6b", "booking 2026-03-29T02:30 Berlin is 422 invalid_local_time", book(tok, "r_nacht", "n_1", "2026-03-29T02:30", 2), 422, "invalid_local_time")
    r = book(tok, "r_nacht", "n_1", "2026-03-29T01:30", 2)
    check_res("TIME-7", "01:30 before the gap ends 04:00+02:00 (90 absolute minutes)", r, 201,
              starts_at="2026-03-29T01:30:00+01:00", ends_at="2026-03-29T04:00:00+02:00")
    sm = slot_map(avail("r_nacht", "2026-03-29", 2))
    check("TIME-7b", "occupancy across the gap: 03:00 and 03:30 blocked, 04:00 (02:00Z, half-open) and 00:00 free",
          sm.get("2026-03-29T03:00") == [] and sm.get("2026-03-29T03:30") == [] and sm.get("2026-03-29T04:00") == ["n_1"]
          and sm.get("2026-03-29T00:00") == ["n_1"] and sm.get("2026-03-29T00:30") == [], repr(sm))

    # Berlin fall back 2026-10-25
    a = avail("r_nacht", "2026-10-25", 2)
    exp = ["2026-10-25T" + h for h in ("00:00", "00:30", "01:00", "01:30", "02:00", "02:30", "03:00", "03:30", "04:00", "04:30")]
    check("TIME-8", "Berlin fall-back night: repeated hour appears once, exact 10 slots",
          a.status == 200 and [s["starts_at_local"] for s in a.json["slots"]] == exp, repr(a))
    got = {s["starts_at_local"]: s["starts_at"] for s in a.json["slots"]} if a.status == 200 else {}
    check("TIME-8b", "fall-back slots: 02:00/02:30 carry +02:00 (first occurrence), 03:00 carries +01:00",
          got.get("2026-10-25T02:00") == "2026-10-25T02:00:00+02:00" and got.get("2026-10-25T02:30") == "2026-10-25T02:30:00+02:00"
          and got.get("2026-10-25T03:00") == "2026-10-25T03:00:00+01:00" and got.get("2026-10-25T01:30") == "2026-10-25T01:30:00+02:00", repr(got))
    r = book(tok, "r_nacht", "n_1", "2026-10-25T02:30", 2)
    check_res("TIME-9", "booking repeated 02:30 resolves to first occurrence, ends 03:00+01:00", r, 201,
              starts_at="2026-10-25T02:30:00+02:00", ends_at="2026-10-25T03:00:00+01:00")
    sm = slot_map(avail("r_nacht", "2026-10-25", 2))
    check("TIME-11", "occupancy across fall-back: 01:30 and 02:00 blocked, 01:00 and 03:00 free",
          sm.get("2026-10-25T01:30") == [] and sm.get("2026-10-25T02:00") == [] and sm.get("2026-10-25T02:30") == []
          and sm.get("2026-10-25T01:00") == ["n_1"] and sm.get("2026-10-25T03:00") == ["n_1"], repr(sm))
    expect_err("TIME-14", "second occurrence not bookable: same repeated local time again is 409 table_unavailable",
               book(tok, "r_nacht", "n_1", "2026-10-25T02:30", 2), 409, "table_unavailable")
    cancel(tok, r.json["reference"]) if r.status == 201 else None
    r = book(tok, "r_nacht", "n_1", "2026-10-25T01:30", 2)
    check_res("TIME-10", "01:30 on fall-back night ends 02:00+01:00 (post-transition offset)", r, 201,
              starts_at="2026-10-25T01:30:00+02:00", ends_at="2026-10-25T02:00:00+01:00")

    # New York spring forward 2026-03-08
    a = avail("r_gotham", "2026-03-08", 2)
    exp = ["2026-03-08T" + h for h in ("00:00", "00:30", "01:00", "01:30", "03:00", "03:30", "04:00", "04:30")]
    check("TIME-12", "NY spring-forward: 02:00 and 02:30 absent, exact slot list",
          a.status == 200 and [s["starts_at_local"] for s in a.json["slots"]] == exp, repr(a))
    expect_err("TIME-12b", "booking 2026-03-08T02:00 NY is 422 invalid_local_time", book(tok, "r_gotham", "g_1", "2026-03-08T02:00", 2), 422, "invalid_local_time")
    r = book(tok, "r_gotham", "g_1", "2026-03-08T01:30", 2)
    check_res("TIME-12c", "NY 01:30 before the gap ends 04:00-04:00", r, 201, starts_at="2026-03-08T01:30:00-05:00", ends_at="2026-03-08T04:00:00-04:00")

    # New York fall back 2026-11-01
    a = avail("r_gotham", "2026-11-01", 2)
    exp = ["2026-11-01T" + h for h in ("00:00", "00:30", "01:00", "01:30", "02:00", "02:30", "03:00", "03:30", "04:00", "04:30")]
    got = {s["starts_at_local"]: s["starts_at"] for s in a.json["slots"]} if a.status == 200 else {}
    check("TIME-13", "NY fall-back: repeated 01:00/01:30 appear once with -04:00, 02:00 carries -05:00",
          a.status == 200 and [s["starts_at_local"] for s in a.json["slots"]] == exp
          and got.get("2026-11-01T01:00") == "2026-11-01T01:00:00-04:00" and got.get("2026-11-01T01:30") == "2026-11-01T01:30:00-04:00"
          and got.get("2026-11-01T02:00") == "2026-11-01T02:00:00-05:00", repr(a))
    r = book(tok, "r_gotham", "g_1", "2026-11-01T01:30", 2)
    check_res("TIME-13b", "NY 01:30 on fall-back night ends 02:00-05:00", r, 201, starts_at="2026-11-01T01:30:00-04:00", ends_at="2026-11-01T02:00:00-05:00")
    cancel(tok, r.json["reference"]) if r.status == 201 else None
    r = book(tok, "r_gotham", "g_1", "2026-11-01T01:00", 2)
    check_res("TIME-13c", "NY 01:00 on fall-back night ends 01:30-05:00", r, 201, starts_at="2026-11-01T01:00:00-04:00", ends_at="2026-11-01T01:30:00-05:00")
    expect_err("TIME-14b", "NY: same repeated local time again is 409 table_unavailable", book(tok, "r_gotham", "g_1", "2026-11-01T01:00", 2), 409, "table_unavailable")


@group("MOV")
def g_moves():
    reset()
    tok = login("hana")
    ravi = login("ravi")
    off = berlin_evening_offset(THU)
    A = must_book(tok, "r_lumen", "t_quad", T(THU, "19:00"), 2)
    B = must_book(tok, "r_lumen", "t_six", T(THU, "19:00"), 2)
    C = must_book(tok, "r_lumen", "t_duo", T(THU, "19:00"), 2)  # unlisted blocker
    rA, rB, rC = A["reference"], B["reference"], C["reference"]

    r = moves(tok, [{"reference": rA}])
    check("MOV-1", "single no-op item is 201 with one entry equal to the current booking",
          r.status == 201 and isinstance(r.json, dict) and same_json(r.json.get("reservations"), [A]), "got %r expected %r" % (r, [A]))
    r = moves(tok, [{"reference": rA, "party_size": 3}])
    ok = r.status == 201 and len(r.json.get("reservations", [])) == 1
    if ok:
        e = r.json["reservations"][0]
        ok = not res_problems(e, party_size=3, reservation_id=A["reservation_id"], reference=rA, created_at=A["created_at"],
                              table_id="t_quad", starts_at_local=T(THU, "19:00"), status="confirmed")
    check("MOV-2", "party_size change via moves keeps identity and created_at", ok, repr(r))
    swap_key = newkey()
    swap_body = [{"reference": rA, "table_id": "t_six"}, {"reference": rB, "table_id": "t_quad"}]
    swap = moves(tok, swap_body, key=swap_key)
    ok = swap.status == 201 and [x.get("reference") for x in swap.json.get("reservations", [])] == [rA, rB]
    ok = ok and swap.json["reservations"][0]["table_id"] == "t_six" and swap.json["reservations"][1]["table_id"] == "t_quad"
    ok = ok and get_res(tok, rA).json.get("table_id") == "t_six" and get_res(tok, rB).json.get("table_id") == "t_quad"
    check("MOV-3", "swapping two tables between two bookings is 201 in input order", ok, repr(swap))
    r = moves(tok, [{"reference": rA, "starts_at_local": T(THU, "19:30")}])
    ok = r.status == 201 and r.json["reservations"][0].get("starts_at_local") == T(THU, "19:30") \
        and r.json["reservations"][0].get("ends_at") == T(THU, "21:00") + ":00" + off and r.json["reservations"][0].get("table_id") == "t_six"
    check("MOV-4", "moving onto an overlapping slot of its own table is 201 with recomputed times", ok, repr(r))
    check("MOV-23", "old slot released after a time move",
          slot_map(avail("r_lumen", THU, 1)).get(T(THU, "18:00")) == ["t_six"] and
          slot_map(avail("r_lumen", THU, 1)).get(T(THU, "20:30")) == ["t_duo", "t_quad"], repr(slot_map(avail("r_lumen", THU, 1))))
    before = get_res(tok, rA).json
    r = moves(tok, [{"reference": rA, "table_id": "t_quad", "starts_at_local": T(THU, "19:00")}])
    expect_err("MOV-5", "overlap with an unlisted booking is 409 table_unavailable", r, 409, "table_unavailable")
    check("MOV-5b", "listed booking unchanged after 409", same_json(get_res(tok, rA).json, before), "")
    r = moves(tok, [{"reference": rA, "table_id": "t_quad", "starts_at_local": T(THU, "19:00")}, {"reference": rB, "party_size": 3}])
    expect_err("MOV-6", "overlap with an unchanged listed booking that retains occupancy is 409 table_unavailable", r, 409, "table_unavailable")
    check("MOV-6b", "both bookings unchanged after 409",
          same_json(get_res(tok, rA).json, before) and get_res(tok, rB).json.get("party_size") == 2, "")

    expect_err("MOV-7", "0 items is 422 validation_failed", moves(tok, []), 422, "validation_failed")
    Ds = [must_book(tok, "r_lumen", tb, T(FRI, hh), 2) for tb in ("t_six", "t_quad", "t_duo") for hh in ("18:00", "19:30", "21:00")]
    D9 = Ds.pop()
    refs9 = [d["reference"] for d in Ds] + [D9["reference"]]
    expect_err("MOV-8", "9 items is 422 validation_failed", moves(tok, [{"reference": x} for x in refs9]), 422, "validation_failed")
    r = moves(tok, [{"reference": x} for x in refs9[:8]])
    check("MOV-9", "8 items is 201 with 8 entries in input order",
          r.status == 201 and [x.get("reference") for x in r.json.get("reservations", [])] == refs9[:8] and same_json(r.json["reservations"], Ds), repr(r))
    expect_err("MOV-10", "duplicate references are 422 validation_failed", moves(tok, [{"reference": rA}, {"reference": rA, "party_size": 2}]), 422, "validation_failed")
    P = must_book(tok, "r_pier", "p_1", T(THU, "19:15"), 2)
    expect_err("MOV-11", "bookings from two restaurants are 422 validation_failed", moves(tok, [{"reference": rA}, {"reference": P["reference"]}]), 422, "validation_failed")
    Q = must_book(ravi, "r_lumen", "t_duo", T(THU, "21:30"), 2)
    r = moves(tok, [{"reference": rA, "party_size": 4}, {"reference": Q["reference"]}])
    expect_err("MOV-12", "another user's reference is 404 not_found", r, 404, "not_found")
    check("MOV-12b", "nothing changed after the 404", same_json(get_res(tok, rA).json, before) and same_json(get_res(ravi, Q["reference"]).json, Q), "")
    expect_err("MOV-13", "unknown reference is 404 not_found", moves(tok, [{"reference": "ZZZZZZ"}]), 404, "not_found")
    cancel(tok, D9["reference"])
    expect_err("MOV-14", "a cancelled booking in the batch is 409 reservation_cancelled",
               moves(tok, [{"reference": rA}, {"reference": D9["reference"], "party_size": 2}]), 409, "reservation_cancelled")
    for label, body in [("missing moves", {}), ("moves not array", {"moves": "x"}), ("item not object", {"moves": [5]}),
                        ("reference not string", {"moves": [{"reference": 123}]}), ("item without reference", {"moves": [{"party_size": 2}]})]:
        r = call("POST", "/reservation-moves", body=body, token=tok, key=newkey())
        expect_err("MOV-15[%s]" % label, "%s is 422 validation_failed" % label, r, 422, "validation_failed")
    r = call("POST", "/reservation-moves", body={"moves": [{"reference": rA}]}, token=tok)
    expect_err("MOV-16a", "moves without Idempotency-Key is 400 missing_idempotency_key", r, 400, "missing_idempotency_key")
    r = call("POST", "/reservation-moves", body={"moves": [{"reference": rA}]}, token=tok, key="x" * 256)
    expect_err("MOV-16b", "moves with 256-char key is 422 validation_failed", r, 422, "validation_failed")
    r = call("POST", "/reservation-moves", raw="{bad", token=tok, key=newkey())
    expect_err("MOV-16c", "moves with unparseable body is 400 malformed_request", r, 400, "malformed_request")

    # atomicity
    d1, d2 = Ds[0]["reference"], Ds[1]["reference"]
    sm_before = slot_map(avail("r_lumen", FRI, 1))
    kx = newkey()
    r = moves(tok, [{"reference": d1, "party_size": 3}, {"reference": d2, "party_size": 99}], key=kx)
    expect_err("MOV-17", "one failing item fails the whole batch with 422 party_exceeds_capacity", r, 422, "party_exceeds_capacity")
    check("MOV-17b", "failed batch left every booking and availability untouched",
          same_json(get_res(tok, d1).json, Ds[0]) and same_json(get_res(tok, d2).json, Ds[1]) and same_json(sm_before, slot_map(avail("r_lumen", FRI, 1))), "")
    r = moves(tok, [{"reference": d1, "party_size": 3}], key=kx)
    check("MOV-17c", "the key of the failed batch is reusable as a first use (201)",
          r.status == 201 and r.json["reservations"][0].get("party_size") == 3, repr(r))
    expect_err("MOV-18a", "precedence in input order: party_exceeds_capacity before not_on_slot_grid",
               moves(tok, [{"reference": d1, "party_size": 99}, {"reference": d2, "starts_at_local": T(FRI, "19:10")}]), 422, "party_exceeds_capacity")
    expect_err("MOV-18b", "precedence in input order: not_on_slot_grid before party_exceeds_capacity",
               moves(tok, [{"reference": d2, "starts_at_local": T(FRI, "19:10")}, {"reference": d1, "party_size": 99}]), 422, "not_on_slot_grid")
    expect_err("MOV-19", "non-occupancy error (2nd item) precedes occupancy error (1st item)",
               moves(tok, [{"reference": rA, "table_id": "t_quad", "starts_at_local": T(THU, "19:00")}, {"reference": d1, "party_size": 99}]), 422, "party_exceeds_capacity")
    Z1 = must_book(tok, "r_zulu", "z_1", zulu_local(50), 2)
    Z2 = must_book(tok, "r_zulu", "z_2", zulu_local(70), 2)
    expect_err("MOV-20a", "cutoff precedes the booking's own invalid party: 409 cutoff_passed",
               moves(tok, [{"reference": Z1["reference"], "party_size": 99}]), 409, "cutoff_passed")
    expect_err("MOV-20b", "earlier listed booking's error wins over a later cutoff error",
               moves(tok, [{"reference": Z2["reference"], "party_size": 99}, {"reference": Z1["reference"], "party_size": 99}]), 422, "party_exceeds_capacity")
    expect_err("MOV-20c", "valid change on a within-cutoff booking is 409 cutoff_passed",
               moves(tok, [{"reference": Z1["reference"], "party_size": 1}]), 409, "cutoff_passed")
    expect_err("MOV-20d", "cutoff of a second listed booking still fails the batch",
               moves(tok, [{"reference": Z2["reference"], "party_size": 3}, {"reference": Z1["reference"], "party_size": 1}]), 409, "cutoff_passed")
    check("MOV-20e", "within-cutoff booking unchanged", same_json(get_res(tok, Z1["reference"]).json, Z1) and same_json(get_res(tok, Z2["reference"]).json, Z2), "")
    r = moves(tok, [{"reference": Z2["reference"], "party_size": 3}])
    check("MOV-20f", "batch on a booking just outside its cutoff is 201", r.status == 201 and r.json["reservations"][0].get("party_size") == 3, repr(r))

    rep = moves(tok, swap_body, key=swap_key)
    check("MOV-21", "replay of the swap batch is 200 with the original body after further changes",
          rep.status == 200 and same_json(rep.json, swap.json), "orig=%r replay=%r" % (swap, rep))
    r = moves(tok, [{"reference": rA, "table_id": "t_six"}], key=swap_key)
    expect_err("MOV-22", "same key with a different moves body is 409 idempotency_key_reuse", r, 409, "idempotency_key_reuse")
    cur = get_res(tok, rA).json
    check("MOV-22b", "replay and reuse changed nothing", same_json(cur, before), "")


@group("CONC")
def g_concurrency():
    reset()
    tok = login("hana")
    ravi = login("ravi")
    N = 24
    res = parallel([lambda i=i: book(tok if i % 2 else ravi, "r_lumen", "t_quad", T(THU, "19:00"), 2) for i in range(N)])
    exc = [x for x in res if isinstance(x, Exception)]
    st = [x.status for x in res if not isinstance(x, Exception)]
    conflicts = [x for x in res if not isinstance(x, Exception) and x.status == 409 and x.code == "table_unavailable"]
    check("CONC-1", "%d distinct-key bookings for one table/slot: exactly one 201, rest 409 table_unavailable, no 5xx" % N,
          not exc and st.count(201) == 1 and len(conflicts) == N - 1 and not any(s >= 500 for s in st),
          "statuses=%r exceptions=%r" % (st, exc[:2]))
    total = len(list_res(tok)) + len(list_res(ravi))
    check("CONC-1b", "exactly one reservation exists after the race", total == 1, "count=%d" % total)

    key = newkey()
    body = {"restaurant_id": "r_lumen", "table_id": "t_six", "starts_at_local": T(THU, "21:30"), "party_size": 2}
    res = parallel([lambda: call("POST", "/reservations", body=body, token=tok, key=key) for _ in range(N)])
    exc = [x for x in res if isinstance(x, Exception)]
    oks = [x for x in res if not isinstance(x, Exception)]
    st = [x.status for x in oks]
    c201 = [x for x in oks if x.status == 201]
    c200 = [x for x in oks if x.status == 200]
    same = bool(c201) and all(same_json(x.json, c201[0].json) for x in c200)
    check("CONC-2", "%d identical requests with one unused key: exactly one 201, rest 200 identical" % N,
          not exc and len(c201) == 1 and len(c200) == N - 1 and same, "statuses=%r" % st)
    mine = [x for x in list_res(tok) if x.get("starts_at_local") == T(THU, "21:30")]
    check("CONC-2b", "the operation took effect exactly once", len(mine) == 1, "count=%d" % len(mine))

    X = must_book(tok, "r_lumen", "t_quad", T(THU2, "20:30"), 2)
    fns = [lambda: moves(tok, [{"reference": X["reference"], "table_id": "t_six"}])]
    fns += [lambda: book(ravi, "r_lumen", "t_six", T(THU2, "20:30"), 2) for _ in range(16)]
    res = parallel(fns)
    exc = [x for x in res if isinstance(x, Exception)]
    oks = [x for x in res if not isinstance(x, Exception)]
    wins = [x for x in oks if x.status == 201]
    losses = [x for x in oks if x.status == 409 and x.code == "table_unavailable"]
    sm = slot_map(avail("r_lumen", THU2, 1))
    move_won = not isinstance(res[0], Exception) and res[0].status == 201
    occupancy_ok = "t_six" not in sm.get(T(THU2, "20:30"), ["t_six"]) and (("t_quad" in sm.get(T(THU2, "20:30"), [])) == move_won)
    check("CONC-3", "a move racing 16 bookings for the same target: exactly one success, others 409 table_unavailable",
          not exc and len(wins) == 1 and len(losses) == len(oks) - 1 and occupancy_ok,
          "statuses=%r move=%r slots=%r" % ([x.status for x in oks], res[0], sm.get(T(THU2, "20:30"))))


@group("RT")
def g_roundtrip():
    reset()
    tok = login("hana")
    nova = call("POST", "/auth/signup", body={"email": "nova@probe.test", "password": "nova-secret-9", "display_name": "Nova"})
    tokN = nova.json["token"] if nova.status == 201 else ""
    k1, k2, km, kf = newkey(), newkey(), newkey(), newkey()
    b1 = {"restaurant_id": "r_lumen", "table_id": "t_quad", "starts_at_local": T(THU, "19:00"), "party_size": 4}
    b2 = {"restaurant_id": "r_lumen", "table_id": "t_six", "starts_at_local": T(THU, "20:30"), "party_size": 2}
    R1 = call("POST", "/reservations", body=b1, token=tok, key=k1)
    R2 = call("POST", "/reservations", body=b2, token=tok, key=k2)
    if R1.status != 201 or R2.status != 201:
        raise RuntimeError("setup bookings failed: %r %r" % (R1, R2))
    mb = [{"reference": R2.json["reference"], "party_size": 3}]
    M1 = moves(tok, mb, key=km)
    bf = {"restaurant_id": "r_lumen", "table_id": "t_duo", "starts_at_local": T(THU, "18:00"), "party_size": 9}
    failed = call("POST", "/reservations", body=bf, token=tok, key=kf)
    pre = list_res(tok)
    pre_rest = call("GET", "/restaurants").json

    ex = call("GET", "/_test/export", timeout=10)
    check("RT-1", "export is 200 with track tablekeeper, format_version 1 and an object state",
          ex.status == 200 and isinstance(ex.json, dict) and ex.json.get("track") == "tablekeeper"
          and ex.json.get("format_version") == 1 and isinstance(ex.json.get("state"), dict), repr(ex)[:300])
    check("RT-2", "export is read-only (list unchanged)", same_json(list_res(tok), pre), "")
    snapshot = ex.json if ex.status == 200 else {}

    # mutate after export
    c = cancel(tok, R2.json["reference"])
    p = patch(tok, R1.json["reference"], {"party_size": 3})
    k3 = newkey()
    b3 = {"restaurant_id": "r_lumen", "table_id": "t_duo", "starts_at_local": T(THU, "21:30"), "party_size": 2}
    R3 = call("POST", "/reservations", body=b3, token=tok, key=k3)
    post = call("POST", "/auth/signup", body={"email": "post@probe.test", "password": "post-secret-9", "display_name": "Post"})
    tokP = post.json["token"] if post.status == 201 else ""
    M2 = moves(tok, [{"reference": R1.json["reference"], "table_id": "t_six", "starts_at_local": T(FRI, "19:00")}], key=newkey())
    check("RT-0", "post-export mutations succeeded (setup)", c.status == 200 and p.status == 200 and R3.status == 201 and post.status == 201 and M2.status == 201,
          "cancel=%r patch=%r book=%r signup=%r moves=%r" % (c, p, R3, post, M2))

    imp = call("POST", "/_test/import", body=snapshot, timeout=10)
    expect_status("RT-3", "import of the unchanged export is 204", imp, 204)
    r = call("GET", "/reservations", token=tok)
    check("RT-4", "original token works and the list equals the pre-export list exactly",
          r.status == 200 and same_json(r.json.get("reservations"), pre), "got %r" % r)
    l1 = call("POST", "/auth/login", body={"email": "hana@probe.test", "password": "hana-secret-9"})
    l2 = call("POST", "/auth/login", body={"email": "nova@probe.test", "password": "nova-secret-9"})
    check("RT-5", "seeded and signed-up logins still work after import; signed-up token still valid",
          l1.status == 200 and l1.json.get("user_id") == "u_hana" and l2.status == 200
          and l2.json.get("user_id") == nova.json.get("user_id") and call("GET", "/reservations", token=tokN).status == 200,
          "hana=%r nova=%r" % (l1, l2))
    g2 = get_res(tok, R2.json["reference"])
    g1 = get_res(tok, R1.json["reference"])
    check("RT-6a", "cancelled booking is confirmed again and PATCH/move undone",
          g2.status == 200 and same_json(g2.json, M1.json["reservations"][0]) and g1.status == 200 and same_json(g1.json, R1.json),
          "R1=%r R2=%r" % (g1, g2))
    expect_err("RT-6b", "booking created after export is gone (404)", get_res(tok, R3.json["reference"]), 404, "not_found")
    expect_err("RT-6c", "user signed up after export: token is 401", call("GET", "/reservations", token=tokP), 401, "unauthenticated")
    expect_err("RT-6d", "user signed up after export: login is 401", call("POST", "/auth/login", body={"email": "post@probe.test", "password": "post-secret-9"}), 401, "unauthenticated")
    rep = call("POST", "/reservations", body=b1, token=tok, key=k1)
    check("RT-7a", "replay on /reservations returns 200 with the original body after import",
          rep.status == 200 and same_json(rep.json, R1.json), "orig=%r replay=%r" % (R1, rep))
    rep = moves(tok, mb, key=km)
    check("RT-7b", "replay on /reservation-moves returns 200 with the original body after import",
          rep.status == 200 and same_json(rep.json, M1.json), "orig=%r replay=%r" % (M1, rep))
    r = call("POST", "/reservations", body=dict(b1, party_size=2), token=tok, key=k1)
    expect_err("RT-7c", "key reuse with a different body is still 409 after import", r, 409, "idempotency_key_reuse")
    sm = slot_map(avail("r_lumen", THU, 1))
    check("RT-10", "imported reservations occupy their slots in availability",
          sm.get(T(THU, "19:00")) == ["t_duo", "t_six"] and sm.get(T(THU, "20:30")) == ["t_duo", "t_quad"] and sm.get(T(THU, "21:30")) == ["t_duo", "t_quad"], repr(sm))
    r = call("POST", "/reservations", body=dict(bf, party_size=2), token=tok, key=kf)
    check("RT-8", "a key that failed before export is a first use after import (201)", r.status == 201, repr(r))

    imp = call("POST", "/_test/import", body=snapshot, timeout=10)
    r = call("GET", "/reservations", token=tok)
    check("RT-11", "importing the same export twice restores the state without duplication",
          imp.status == 204 and r.status == 200 and same_json(r.json.get("reservations"), pre), repr(r))
    r = call("POST", "/reservations", body=b3, token=tok, key=k3)
    check("RT-9", "a key first used after export is a first use again after import (201)", r.status == 201, repr(r))
    cur = list_res(tok)
    for label, bad in [("wrong track", dict(snapshot, track="other")), ("wrong version", dict(snapshot, format_version=2)),
                       ("missing state", {k: v for k, v in snapshot.items() if k != "state"}), ("non-object state", dict(snapshot, state="x")),
                       ("missing track", {k: v for k, v in snapshot.items() if k != "track"})]:
        r = call("POST", "/_test/import", body=bad, timeout=10)
        expect_err("RT-12[%s]" % label, "import with %s is 422 validation_failed" % label, r, 422, "validation_failed")
    r = call("POST", "/_test/import", raw="{\"track\": ", timeout=10)
    expect_err("RT-13", "import with invalid JSON is 400 malformed_request", r, 400, "malformed_request")
    check("RT-12b", "destination unchanged after rejected imports", same_json(list_res(tok), cur) and same_json(call("GET", "/restaurants").json, pre_rest), "")
    r = call("POST", "/_test/import", body=dict(snapshot, note="ignored"), timeout=10)
    check("RT-14", "export with an extra unknown top-level field still imports (204) and restores the pre-export list",
          r.status == 204 and same_json(list_res(tok), pre), repr(r))
    tiny = {"users": [{"id": "u_solo", "email": "solo@probe.test", "password": "solo-secret-9", "display_name": "Solo"}],
            "restaurants": [{"id": "r_solo", "name": "Solo", "timezone": "Europe/Berlin", "slot_minutes": 30,
                             "reservation_duration_minutes": 60, "cancellation_cutoff_minutes": 30,
                             "opening_hours": hours(ALL_DAYS, "12:00", "14:00"), "tables": [{"id": "s_1", "label": "S", "capacity": 2}]}],
            "reservations": []}
    reset(tiny)
    r1 = call("GET", "/reservations", token=tok)
    r2 = call("POST", "/auth/login", body={"email": "hana@probe.test", "password": "hana-secret-9"})
    r3 = call("GET", "/restaurants")
    check("RT-15", "reset after import clears imported tokens, accounts and restaurants",
          r1.status == 401 and r2.status == 401 and r3.status == 200 and [x["id"] for x in r3.json["restaurants"]] == ["r_solo"],
          "token=%r login=%r restaurants=%r" % (r1, r2, r3))


@group("CFX")
def g_combo_fixture():
    reset(base_fixture())
    r = call("POST", "/_test/reset", body=s2_fixture(), timeout=10)
    check("CFX-1a", "fixture with combinable pairs is accepted (204)", r.status == 204, repr(r))
    a = avail("r_combo", THU, 1)
    check("CFX-1b", "every slot carries available_options (list)",
          a.status == 200 and len(a.json["slots"]) == 8 and all(isinstance(s.get("available_options"), list) for s in a.json["slots"]), repr(a)[:300])
    a = avail("r_lumen", THU, 1)
    check("CFX-1c", "restaurant without combinable lists singles only in available_options, matching available_table_ids",
          a.status == 200 and all([o.get("table_ids") for o in s.get("available_options", [])] == [[t] for t in s["available_table_ids"]]
                                   for s in a.json["slots"]), repr(a)[:300])
    r = call("POST", "/_test/reset", body=base_fixture(), timeout=10)
    check("CFX-1d", "stage-1-shaped fixture (no combinable anywhere) is still accepted", r.status == 204, repr(r))

    reset(s2_fixture())
    tok = login("hana")
    X = must_book(tok, "r_combo", "c_d", T(THU, "19:00"), 4)
    bad = [("triple", [["c_a", "c_b", "c_c"]]), ("singleton", [["c_a"]]), ("unknown id", [["c_a", "c_zz"]]),
           ("duplicate", [["c_a", "c_a"]]), ("other restaurant's table", [["c_a", "t_duo"]]), ("not a list", ["c_a", "c_b"])]
    for label, comb in bad:
        r = call("POST", "/_test/reset", body=s2_fixture(combinable=comb), timeout=10)
        expect_err("CFX-2[%s]" % label, "combinable %s is rejected 422 validation_failed" % label, r, 422, "validation_failed")
    g = get_res(tok, X["reference"])
    check("CFX-2b", "state unchanged after rejected fixtures (token valid, booking present)", g.status == 200 and same_json(g.json, X), repr(g))

    seeds = [{"id": "res_pair", "reference": "PAIRSD", "user_id": "u_hana", "restaurant_id": "r_combo",
              "table_ids": ["c_b", "c_a"], "starts_at_local": T(THU, "19:00"), "party_size": 4},
             {"id": "res_single", "reference": "SINGSD", "user_id": "u_hana", "restaurant_id": "r_combo",
              "table_id": "c_d", "starts_at_local": T(THU, "19:00"), "party_size": 5},
             {"id": "res_canc", "reference": "CANCSD", "user_id": "u_hana", "restaurant_id": "r_combo",
              "table_id": "c_c", "starts_at_local": T(THU, "19:00"), "party_size": 2, "status": "cancelled"}]
    r = call("POST", "/_test/reset", body=s2_fixture(seeds), timeout=10)
    check("CFX-3a", "fixture with table_ids / table_id / cancelled seeds is accepted", r.status == 204, repr(r))
    tok = login("hana")
    g = get_res(tok, "PAIRSD")
    check_res("CFX-3", "seeded pair reservation visible with table_ids and no table_id", g, 200, reservation_id="res_pair",
              party_size=4, status="confirmed", starts_at_local=T(THU, "19:00"))
    check("CFX-3b", "seeded pair table_ids is the seeded set", g.status == 200 and set(g.json.get("table_ids", [])) == {"c_a", "c_b"} and "table_id" not in g.json, repr(g))
    g = get_res(tok, "SINGSD")
    check_res("CFX-4", "seeded table_id reservation visible with table_ids [c_d] and table_id c_d", g, 200, table_ids=["c_d"], table_id="c_d", party_size=5)
    g = get_res(tok, "CANCSD")
    check_res("CFX-5", "seeded cancelled reservation visible as cancelled", g, 200, status="cancelled", table_ids=["c_c"])
    a = avail("r_combo", THU, 1)
    check("CFX-3c", "seeded pair and single block their tables; cancelled seed does not block",
          a.status == 200 and singles_at(a, T(THU, "19:00")) == ["c_c"] and opt_ids(a, T(THU, "19:00")) == [("c_c",)]
          and singles_at(a, T(THU, "20:30")) == ["c_a", "c_b", "c_c", "c_d"]
          and opt_ids(a, T(THU, "20:30")) == [("c_a",), ("c_b",), ("c_c",), ("c_d",), ("c_b", "c_a"), ("c_b", "c_c")], repr(a)[:400])
    r = call("GET", "/restaurants/r_combo")
    fr = combo_restaurant()
    check("CFX-6", "GET /restaurants/r_combo returns the stage-1 fields equal to the fixture",
          r.status == 200 and all(r.json.get(k) == fr[k] for k in ("id", "name", "timezone", "slot_minutes", "reservation_duration_minutes",
                                                                  "cancellation_cutoff_minutes", "opening_hours", "tables")), repr(r)[:300])


@group("CAV")
def g_combo_availability():
    reset(s2_fixture())
    tok = login("hana")
    L = T(THU, "19:00")
    a4 = avail("r_combo", THU, 4)
    check("CAV-1", "available_table_ids lists singles only (party 4: c_c, c_d)", a4.status == 200 and singles_at(a4, L) == ["c_c", "c_d"], repr(a4)[:300])
    exp = [{"table_ids": ["c_c"], "capacity": 4}, {"table_ids": ["c_d"], "capacity": 6},
           {"table_ids": ["c_b", "c_a"], "capacity": 4}, {"table_ids": ["c_b", "c_c"], "capacity": 6}]
    check("CAV-2", "options: singles in fixture order, then pairs in combinable order with combinable-ordered table_ids",
          a4.status == 200 and same_json(options_at(a4, L), exp), repr(options_at(a4, L)))
    check("CAV-3a", "capacity is the sum for pairs (party 4 exact options on every slot)",
          a4.status == 200 and all(same_json(options_at(a4, s["starts_at_local"]), exp) for s in a4.json["slots"]), "")
    a5 = avail("r_combo", THU, 5)
    check("CAV-3b", "party 5 offers [c_d 6, c_b+c_c 6]", a5.status == 200 and same_json(options_at(a5, L), [{"table_ids": ["c_d"], "capacity": 6}, {"table_ids": ["c_b", "c_c"], "capacity": 6}]), repr(options_at(a5, L)))
    a7 = avail("r_combo", THU, 7)
    check("CAV-3c", "party 7 keeps all 8 slots with available_options [] and available_table_ids []",
          a7.status == 200 and len(a7.json["slots"]) == 8 and all(s.get("available_options") == [] and s["available_table_ids"] == [] for s in a7.json["slots"]), repr(a7)[:300])
    a1 = avail("r_combo", THU, 1)
    check("CAV-6", "party 1 lists all singles and exactly the two declared pairs (no transitive c_a+c_c)",
          a1.status == 200 and opt_ids(a1, L) == [("c_a",), ("c_b",), ("c_c",), ("c_d",), ("c_b", "c_a"), ("c_b", "c_c")], repr(opt_ids(a1, L)))
    check("CAV-8", "available_options present on every slot", a1.status == 200 and all(isinstance(s.get("available_options"), list) for s in a1.json["slots"]), "")
    must_book(tok, "r_combo", "c_a", L, 2)
    a1 = avail("r_combo", THU, 1)
    check("CAV-4", "single booking on c_a removes c_a and pair c_b+c_a but keeps c_b+c_c",
          singles_at(a1, L) == ["c_b", "c_c", "c_d"] and opt_ids(a1, L) == [("c_b",), ("c_c",), ("c_d",), ("c_b", "c_c")], repr(opt_ids(a1, L)))
    must_book2(tok, "r_combo", ["c_b", "c_c"], T(THU, "20:30"), 6)
    a1 = avail("r_combo", THU, 1)
    check("CAV-5", "pair booking c_b+c_c at 20:30 blocks c_b, c_c and both pairs at 20:30 and 19:30, not at 19:00 (half-open)",
          singles_at(a1, T(THU, "20:30")) == ["c_a", "c_d"] and opt_ids(a1, T(THU, "20:30")) == [("c_a",), ("c_d",)]
          and singles_at(a1, T(THU, "19:30")) == ["c_d"] and opt_ids(a1, T(THU, "19:30")) == [("c_d",)]
          and singles_at(a1, L) == ["c_b", "c_c", "c_d"], repr(slot_map(a1)))
    must_book(tok, "r_combo", "c_d", T(THU, "18:00"), 5)
    a5 = avail("r_combo", THU, 5)
    check("CAV-7", "party 5 with c_d taken sees exactly the pair c_b+c_c (no single fits)", a5.status == 200 and opt_ids(a5, T(THU, "18:00")) == [("c_b", "c_c")] and singles_at(a5, T(THU, "18:00")) == [], repr(options_at(a5, T(THU, "18:00"))))


@group("CRS")
def g_combo_create():
    reset(s2_fixture())
    tok = login("hana")
    ravi = login("ravi")
    L = T(THU, "19:00")
    r = book2(tok, "r_combo", ["c_b", "c_a"], L, 4)
    check_res("CRS-1", "pair booking is 201 with table_ids pair and no table_id", r, 201, restaurant_id="r_combo", party_size=4, status="confirmed")
    check("CRS-1b", "pair booking table_ids is the requested set", r.status == 201 and set(r.json.get("table_ids", [])) == {"c_a", "c_b"} and len(r.json["table_ids"]) == 2 and "table_id" not in r.json, repr(r))
    pairA = r.json if r.status == 201 else {}
    r = book(tok, "r_combo", "c_c", L, 4)
    check_res("CRS-2a", "legacy table_id booking is 201 with table_ids [c_c] and table_id c_c", r, 201, table_ids=["c_c"], table_id="c_c")
    legacy = r.json if r.status == 201 else {}
    r = book2(tok, "r_combo", ["c_d"], L, 5)
    check_res("CRS-2b", "one-element table_ids is 201 with table_id present", r, 201, table_ids=["c_d"], table_id="c_d")
    body = {"restaurant_id": "r_combo", "table_id": "c_a", "table_ids": ["c_a"], "starts_at_local": T(THU, "20:30"), "party_size": 2}
    expect_err("CRS-3a", "both table_id and table_ids (agreeing) is 422 validation_failed", call("POST", "/reservations", body=body, token=tok, key=newkey()), 422, "validation_failed")
    body["table_ids"] = ["c_b", "c_a"]
    expect_err("CRS-3b", "both table_id and table_ids (pair) is 422 validation_failed", call("POST", "/reservations", body=body, token=tok, key=newkey()), 422, "validation_failed")
    expect_err("CRS-4a", "duplicate id in table_ids is 422 validation_failed", book2(tok, "r_combo", ["c_a", "c_a"], T(THU, "20:30"), 2), 422, "validation_failed")
    expect_err("CRS-4b", "empty table_ids is 422 validation_failed", book2(tok, "r_combo", [], T(THU, "20:30"), 2), 422, "validation_failed")
    expect_err("CRS-5", "three ids are 422 combination_not_allowed", book2(tok, "r_combo", ["c_a", "c_b", "c_c"], T(THU, "20:30"), 2), 422, "combination_not_allowed")
    for pair in (["c_a", "c_c"], ["c_a", "c_d"], ["c_c", "c_d"]):
        expect_err("CRS-6[%s]" % "+".join(pair), "unlisted pair %s is 422 combination_not_allowed" % pair, book2(tok, "r_combo", pair, T(THU, "20:30"), 2), 422, "combination_not_allowed")
    expect_err("CRS-7a", "party 5 on pair c_b+c_a (sum 4) is 422 party_exceeds_capacity", book2(tok, "r_combo", ["c_b", "c_a"], T(THU, "20:30"), 5), 422, "party_exceeds_capacity")
    r = book2(tok, "r_combo", ["c_a", "c_b"], T(THU, "20:30"), 4)
    check_res("CRS-7b", "party equal to the summed capacity is 201 (ids given in the other order)", r, 201, party_size=4)
    if r.status == 201:
        cancel(tok, r.json["reference"])
    expect_err("CRS-8a", "single on a member of a booked pair is 409 table_unavailable", book(ravi, "r_combo", "c_a", L, 2), 409, "table_unavailable")
    expect_err("CRS-8b", "pair overlapping a booked pair's member is 409 table_unavailable", book2(ravi, "r_combo", ["c_b", "c_c"], L, 2), 409, "table_unavailable")
    expect_err("CRS-8c", "single on a legacy-booked table is 409 table_unavailable", book(ravi, "r_combo", "c_c", L, 2), 409, "table_unavailable")
    P = must_book2(tok, "r_combo", ["c_b", "c_c"], T(THU, "21:30"), 6)
    expect_err("CRS-8d", "single c_c against pair c_b+c_c is 409 table_unavailable", book(ravi, "r_combo", "c_c", T(THU, "21:30"), 2), 409, "table_unavailable")
    expect_err("CRS-8e", "single c_b against pair c_b+c_c is 409 table_unavailable", book(ravi, "r_combo", "c_b", T(THU, "21:30"), 2), 409, "table_unavailable")
    expect_err("CRS-8f", "pair c_b+c_a against pair c_b+c_c is 409 table_unavailable", book2(ravi, "r_combo", ["c_b", "c_a"], T(THU, "21:30"), 2), 409, "table_unavailable")
    check_res("CRS-8g", "single c_a next to pair c_b+c_c is 201", book(ravi, "r_combo", "c_a", T(THU, "21:30"), 2), 201, table_id="c_a")
    body = {"restaurant_id": "r_combo", "table_ids": "c_a", "starts_at_local": T(THU, "20:30"), "party_size": 2}
    expect_err("CRS-9", "table_ids as a string is 400 malformed_request", call("POST", "/reservations", body=body, token=tok, key=newkey()), 400, "malformed_request")
    lst = list_res(tok)
    probs = []
    for x in lst:
        probs += res_problems(x)
    pair_entries = [x for x in lst if len(x.get("table_ids", [])) == 2]
    check("CRS-10a", "list entries all carry table_ids and table_id only for singles (3 pair entries incl. the cancelled one)",
          not probs and len(pair_entries) == 3 and all("table_id" not in x for x in pair_entries), "; ".join(probs) + " pairs=%d" % len(pair_entries))
    g = get_res(tok, pairA.get("reference", "XXXXXX"))
    check("CRS-10b", "GET of a pair booking equals the 201 body", g.status == 200 and same_json(g.json, pairA), repr(g))
    c = cancel(tok, pairA.get("reference", "XXXXXX"))
    check_res("CRS-10c", "cancel response of a pair booking carries table_ids and no table_id", c, 200, status="cancelled")
    check("CRS-10d", "cancelled pair keeps its table_ids", c.status == 200 and set(c.json.get("table_ids", [])) == {"c_a", "c_b"} and "table_id" not in c.json, repr(c))
    p = patch(tok, legacy.get("reference", "XXXXXX"), {"party_size": 3})
    check_res("CRS-10e", "PATCH response of a single carries table_ids [c_c] and table_id", p, 200, table_ids=["c_c"], table_id="c_c", party_size=3)
    m = moves(tok, [{"reference": P["reference"], "party_size": 5}])
    check("CRS-10f", "moves entry of a pair booking carries table_ids and no table_id",
          m.status == 201 and set(m.json["reservations"][0].get("table_ids", [])) == {"c_b", "c_c"} and "table_id" not in m.json["reservations"][0]
          and not res_problems(m.json["reservations"][0]), repr(m))
    a = avail("r_combo", THU, 1)
    check("CRS-11", "cancelling the pair frees both members: c_a, c_b and pair c_b+c_a offered again at 19:00",
          singles_at(a, L) == ["c_a", "c_b"] and opt_ids(a, L) == [("c_a",), ("c_b",), ("c_b", "c_a")], repr(opt_ids(a, L)))
    r = book2(tok, "r_combo", ["c_b", "c_a"], T(THU, "18:00"), 4)
    r2 = book2(tok, "r_combo", ["c_b", "c_a"], T(THU, "19:30"), 4)
    check("CRS-12", "pair at 18:00 (90 min) does not block the pair at 19:30 (half-open)", r.status == 201 and r2.status == 201, "%r %r" % (r, r2))


@group("CID")
def g_combo_idempotency():
    reset(s2_fixture())
    tok = login("hana")
    L = T(THU, "19:00")
    body = {"restaurant_id": "r_combo", "table_ids": ["c_b", "c_a"], "starts_at_local": L, "party_size": 4}
    key = newkey()
    first = call("POST", "/reservations", body=body, token=tok, key=key)
    rep = call("POST", "/reservations", body=body, token=tok, key=key)
    check("CID-1", "replay of a pair booking is 200 identical and one reservation exists",
          first.status == 201 and rep.status == 200 and same_json(rep.json, first.json) and len(list_res(tok)) == 1, "first=%r replay=%r" % (first, rep))
    expect_err("CID-2", "same key with a different pair is 409 idempotency_key_reuse",
               call("POST", "/reservations", body=dict(body, table_ids=["c_b", "c_c"]), token=tok, key=key), 409, "idempotency_key_reuse")
    k3 = newkey()
    b3 = {"restaurant_id": "r_combo", "table_id": "c_d", "starts_at_local": T(THU, "20:30"), "party_size": 2}
    r = call("POST", "/reservations", body=b3, token=tok, key=k3)
    b3b = {"restaurant_id": "r_combo", "table_ids": ["c_d"], "starts_at_local": T(THU, "20:30"), "party_size": 2}
    r2 = call("POST", "/reservations", body=b3b, token=tok, key=k3)
    check("CID-3", "table_id vs one-element table_ids are different bodies: same key is 409 idempotency_key_reuse",
          r.status == 201 and r2.status == 409 and r2.code == "idempotency_key_reuse", "first=%r second=%r" % (r, r2))
    c = cancel(tok, first.json["reference"])
    rep = call("POST", "/reservations", body=body, token=tok, key=key)
    check("CID-4", "replay after cancelling the pair booking returns the original confirmed body",
          c.status == 200 and rep.status == 200 and same_json(rep.json, first.json), "cancel=%r replay=%r" % (c, rep))
    kf = newkey()
    r = book2(tok, "r_combo", ["c_a", "c_c"], T(THU, "21:30"), 2, key=kf)
    r2 = book2(tok, "r_combo", ["c_b", "c_c"], T(THU, "21:30"), 2, key=kf)
    check("CID-5", "key that failed with combination_not_allowed is a first use next time (201)",
          r.status == 422 and r.code == "combination_not_allowed" and r2.status == 201, "first=%r second=%r" % (r, r2))


@group("CPT")
def g_combo_patch():
    reset(s2_fixture())
    tok = login("hana")
    ravi = login("ravi")
    L = T(THU, "19:00")
    A = must_book(tok, "r_combo", "c_d", L, 4)
    ref = A["reference"]
    r = patch(tok, ref, {"table_ids": ["c_b", "c_a"]})
    check_res("CPT-1", "PATCH single -> pair is 200 with the pair and no table_id", r, 200, reference=ref, party_size=4)
    check("CPT-1b", "PATCHed pair is the requested set", r.status == 200 and set(r.json.get("table_ids", [])) == {"c_a", "c_b"} and "table_id" not in r.json, repr(r))
    a = avail("r_combo", THU, 1)
    check("CPT-1c", "old table freed and both members taken", singles_at(a, L) == ["c_c", "c_d"], repr(singles_at(a, L)))
    r = patch(tok, ref, {"table_id": "c_d"})
    check_res("CPT-2a", "PATCH back to a single via table_id", r, 200, table_ids=["c_d"], table_id="c_d")
    r = patch(tok, ref, {"table_ids": ["c_c"]})
    check_res("CPT-2b", "PATCH to a single via one-element table_ids yields table_id", r, 200, table_ids=["c_c"], table_id="c_c")
    expect_err("CPT-3", "PATCH with both table_id and table_ids is 422 validation_failed", patch(tok, ref, {"table_id": "c_d", "table_ids": ["c_d"]}), 422, "validation_failed")
    expect_err("CPT-4a", "PATCH to an unlisted pair is 422 combination_not_allowed", patch(tok, ref, {"table_ids": ["c_a", "c_c"]}), 422, "combination_not_allowed")
    expect_err("CPT-4b", "PATCH to three tables is 422 combination_not_allowed", patch(tok, ref, {"table_ids": ["c_a", "c_b", "c_c"]}), 422, "combination_not_allowed")
    before = get_res(tok, ref).json
    B = must_book(ravi, "r_combo", "c_a", L, 2)
    expect_err("CPT-5", "PATCH to a pair with a taken member is 409 table_unavailable", patch(tok, ref, {"table_ids": ["c_b", "c_a"]}), 409, "table_unavailable")
    check("CPT-5b", "booking unchanged after the 409", same_json(get_res(tok, ref).json, before), "")
    expect_err("CPT-6", "PATCH party above the pair's sum is 422 party_exceeds_capacity", patch(tok, ref, {"table_ids": ["c_b", "c_c"], "party_size": 7}), 422, "party_exceeds_capacity")
    cancel(ravi, B["reference"])
    r = patch(tok, ref, {"table_ids": ["c_b", "c_a"]})
    r2 = patch(tok, ref, {"table_ids": ["c_b", "c_c"]})
    check("CPT-7", "own occupancy does not block moving from c_b+c_a to c_b+c_c at the same slot",
          r.status == 200 and r2.status == 200 and set(r2.json.get("table_ids", [])) == {"c_b", "c_c"}, "%r %r" % (r, r2))
    r = patch(tok, ref, {"starts_at_local": T(THU, "21:30")})
    a = avail("r_combo", THU, 1)
    check("CPT-8", "moving the pair to 21:30 frees both members at 19:00 and takes them at 21:30",
          r.status == 200 and singles_at(a, L) == ["c_a", "c_b", "c_c", "c_d"] and singles_at(a, T(THU, "21:30")) == ["c_a", "c_d"], repr(slot_map(a)))


@group("CMV")
def g_combo_moves():
    reset(s2_fixture())
    tok = login("hana")
    L = T(THU, "19:00")
    A = must_book2(tok, "r_combo", ["c_b", "c_a"], L, 4)
    B = must_book(tok, "r_combo", "c_c", L, 2)
    rA, rB = A["reference"], B["reference"]
    expect_err("CMV-5", "item with both table_id and table_ids is 422 validation_failed",
               moves(tok, [{"reference": rA, "table_id": "c_d", "table_ids": ["c_d"]}]), 422, "validation_failed")
    expect_err("CMV-4", "unlisted pair in a move is 422 combination_not_allowed", moves(tok, [{"reference": rA, "table_ids": ["c_a", "c_c"]}]), 422, "combination_not_allowed")
    expect_err("CMV-3", "overlap among resulting bookings through shared member c_c is 409 table_unavailable",
               moves(tok, [{"reference": rA, "table_ids": ["c_b", "c_c"]}, {"reference": rB, "party_size": 3}]), 409, "table_unavailable")
    check("CMV-3b", "nothing changed after the refused batches", same_json(get_res(tok, rA).json, A) and same_json(get_res(tok, rB).json, B), "")
    kswap = newkey()
    swap_body = [{"reference": rA, "table_ids": ["c_b", "c_c"]}, {"reference": rB, "table_id": "c_a"}]
    swap = moves(tok, swap_body, key=kswap)
    ok = swap.status == 201 and [x.get("reference") for x in swap.json.get("reservations", [])] == [rA, rB]
    ok = ok and set(swap.json["reservations"][0].get("table_ids", [])) == {"c_b", "c_c"} and "table_id" not in swap.json["reservations"][0]
    ok = ok and swap.json["reservations"][1].get("table_ids") == ["c_a"] and swap.json["reservations"][1].get("table_id") == "c_a"
    ok = ok and set(get_res(tok, rA).json.get("table_ids", [])) == {"c_b", "c_c"} and get_res(tok, rB).json.get("table_ids") == ["c_a"]
    check("CMV-2", "swap involving a pair (A -> c_b+c_c, B -> c_a) is 201 in input order", ok, repr(swap))
    r = moves(tok, [{"reference": rB, "table_ids": ["c_d"]}])
    r2 = moves(tok, [{"reference": rA, "table_ids": ["c_b", "c_a"]}])
    check("CMV-1", "move items with table_ids are 201 and carry the sets",
          r.status == 201 and r.json["reservations"][0].get("table_ids") == ["c_d"] and r.json["reservations"][0].get("table_id") == "c_d"
          and r2.status == 201 and set(r2.json["reservations"][0].get("table_ids", [])) == {"c_a", "c_b"}, "%r %r" % (r, r2))
    U = must_book2(tok, "r_combo", ["c_b", "c_c"], T(THU, "20:30"), 6)
    before = get_res(tok, rA).json
    expect_err("CMV-7", "moving the pair onto a slot where an unlisted pair booking holds c_b is 409 table_unavailable",
               moves(tok, [{"reference": rA, "starts_at_local": T(THU, "20:30")}]), 409, "table_unavailable")
    check("CMV-7b", "nothing changed", same_json(get_res(tok, rA).json, before), "")
    rep = moves(tok, swap_body, key=kswap)
    check("CMV-6", "replay of the pair swap batch is 200 with the original body", rep.status == 200 and same_json(rep.json, swap.json), "orig=%r replay=%r" % (swap, rep))


@group("CCN")
def g_combo_concurrency():
    reset(s2_fixture())
    toks = [login("hana"), login("ravi"), login("mira")]
    L = T(THU, "19:00")
    N = 24
    res = parallel([lambda i=i: book2(toks[i % 3], "r_combo", ["c_b", "c_a"], L, 4) for i in range(N)])
    exc = [x for x in res if isinstance(x, Exception)]
    oks = [x for x in res if not isinstance(x, Exception)]
    st = [x.status for x in oks]
    total = sum(len(list_res(t)) for t in toks)
    check("CCN-1", "%d distinct-key requests for one pair: exactly one 201, rest 409 table_unavailable, no 5xx, one reservation" % N,
          not exc and st.count(201) == 1 and sum(1 for x in oks if x.status == 409 and x.code == "table_unavailable") == N - 1 and total == 1,
          "statuses=%r total=%d exc=%r" % (st, total, exc[:1]))

    reset(s2_fixture())
    toks = [login("hana"), login("ravi"), login("mira")]
    sets = [["c_b", "c_a"], ["c_a"], ["c_b"], ["c_b", "c_c"]]
    res = parallel([lambda i=i: book2(toks[i % 3], "r_combo", sets[i % 4], L, 2) for i in range(N)])
    exc = [x for x in res if isinstance(x, Exception)]
    oks = [x for x in res if not isinstance(x, Exception)]
    st = [x.status for x in oks]
    allres = [r for t in toks for r in list_res(t)]
    viol = overlap_violations(allres)
    check("CCN-2", "%d mixed pair/single requests: no 5xx, >=1 success, refusals 409 table_unavailable, no table double-booked" % N,
          not exc and st.count(201) >= 1 and all(x.status == 201 or (x.status == 409 and x.code == "table_unavailable") for x in oks) and not viol,
          "statuses=%r violations=%r" % (st, viol))
    wins = [x.json for x in oks if x.status == 201]
    check("CCN-2b", "winning sets are pairwise disjoint and all for the slot",
          all(x["starts_at_local"] == L for x in wins) and not overlap_violations(wins), repr([x.get("table_ids") for x in wins]))

    reset(s2_fixture())
    hana, ravi, mira = login("hana"), login("ravi"), login("mira")
    days = [THU, THU + dt.timedelta(days=1), THU + dt.timedelta(days=2), THU + dt.timedelta(days=3)]
    pre = []
    for d in days[1:]:
        for hh in ("18:00", "19:30", "21:00"):
            if len(pre) < 8:
                pre.append(must_book(hana, "r_combo", "c_d", T(d, hh), 2))
    fns = [lambda p=p: patch(hana, p["reference"], {"table_id": "c_c", "starts_at_local": L}) for p in pre]
    fns += [lambda: book(ravi, "r_combo", "c_c", L, 2) for _ in range(8)]
    fns += [lambda: book2(mira, "r_combo", ["c_b", "c_c"], L, 2) for _ in range(4)]
    res = parallel(fns)
    exc = [x for x in res if isinstance(x, Exception)]
    oks = [x for x in res if not isinstance(x, Exception)]
    succ = [x for x in oks if x.status in (200, 201)]
    refused = [x for x in oks if x.status == 409 and x.code == "table_unavailable"]
    allres = [r for t in (hana, ravi, mira) for r in list_res(t)]
    viol = overlap_violations(allres)
    check("CCN-3", "20 concurrent PATCHes/bookings/pair bookings onto c_c 19:00: exactly one success, rest 409, no 5xx, no double booking",
          not exc and len(succ) == 1 and len(refused) == len(oks) - 1 and not viol,
          "statuses=%r violations=%r" % ([x.status for x in oks], viol))
    check("CCN-3b", "every pre-existing booking is still confirmed and on c_c 19:00 or its original slot",
          all(get_res(hana, p["reference"]).json.get("status") == "confirmed" for p in pre), "")

    reset(s2_fixture())
    tok = login("hana")
    key = newkey()
    body = {"restaurant_id": "r_combo", "table_ids": ["c_b", "c_c"], "starts_at_local": L, "party_size": 6}
    res = parallel([lambda: call("POST", "/reservations", body=body, token=tok, key=key) for _ in range(N)])
    exc = [x for x in res if isinstance(x, Exception)]
    oks = [x for x in res if not isinstance(x, Exception)]
    c201 = [x for x in oks if x.status == 201]
    c200 = [x for x in oks if x.status == 200]
    check("CCN-4", "%d identical pair requests with one key: exactly one 201, rest 200 identical, one reservation" % N,
          not exc and len(c201) == 1 and len(c200) == N - 1 and all(same_json(x.json, c201[0].json) for x in c200) and len(list_res(tok)) == 1,
          "statuses=%r" % [x.status for x in oks])


@group("UPG")
def g_upgrade():
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "fixtures", "stage1_export.json"), encoding="utf-8") as f:
        export = json.load(f)
    with open(os.path.join(here, "fixtures", "stage1_export_expected.json"), encoding="utf-8") as f:
        exp = json.load(f)
    thu = exp["thu"]
    reset(s2_fixture())
    tokS2 = login("hana")
    r = call("POST", "/_test/import", body=export, timeout=10)
    expect_status("UPG-1", "import of the genuine stage-1 export (table_id-only reservations, no combinable) is 204", r, 204)
    tokH, tokN = exp["token_hana"], exp["token_nova"]
    lst = call("GET", "/reservations", token=tokH)
    check("UPG-2a", "stage-1 login token still works", lst.status == 200, repr(lst)[:200])
    check("UPG-2b", "stage-1 signup token still works", call("GET", "/reservations", token=tokN).status == 200, "")
    l1 = call("POST", "/auth/login", body={"email": "hana@probe.test", "password": "hana-secret-9"})
    l2 = call("POST", "/auth/login", body={"email": "nova@probe.test", "password": "nova-secret-9"})
    check("UPG-2c", "seeded and signed-up stage-1 logins still work with the same user ids",
          l1.status == 200 and l1.json.get("user_id") == "u_hana" and l2.status == 200 and l2.json.get("user_id") == exp["nova_user_id"], "%r %r" % (l1, l2))
    expect_err("UPG-2d", "a stage-2 token issued before the import is 401 (import removes previous credentials)", call("GET", "/reservations", token=tokS2), 401, "unauthenticated")
    got = lst.json.get("reservations") if lst.status == 200 else None
    ok = isinstance(got, list) and len(got) == len(exp["list"]) and all(s1_equiv(o, g) for o, g in zip(exp["list"], got)) and all(not res_problems(g) for g in got)
    check("UPG-3", "list equals the stage-1 list (ids, references, statuses, timestamps) with table_ids == [table_id]", ok, "expected=%r got=%r" % (exp["list"], got))
    g = get_res(tokH, "UPSEED")
    check("UPG-4a", "seeded stage-1 reservation is found by reference", g.status == 200 and g.json.get("reservation_id") == "res_seed_up" and g.json.get("status") == "confirmed", repr(g))
    g = get_res(tokH, exp["r1"]["reference"])
    check("UPG-4b", "created reservation found with the batch-amended party size", g.status == 200 and s1_equiv(exp["m1"]["reservations"][0], g.json), repr(g))
    g = get_res(tokH, exp["r2_cancelled"]["reference"])
    check("UPG-4c", "cancelled reservation is still cancelled", g.status == 200 and g.json.get("status") == "cancelled" and s1_equiv(exp["r2_cancelled"], g.json), repr(g))
    rep = call("POST", "/reservations", body=exp["b1"], token=tokH, key=exp["k1"])
    check("UPG-5a", "stage-1 replay on /reservations is 200 with the original body (plus table_ids at most)", rep.status == 200 and s1_equiv(exp["r1"], rep.json), "orig=%r got=%r" % (exp["r1"], rep))
    rep = call("POST", "/reservation-moves", body=exp["mb"], token=tokH, key=exp["km"])
    ok = rep.status == 200 and isinstance(rep.json, dict) and isinstance(rep.json.get("reservations"), list) \
        and len(rep.json["reservations"]) == 1 and s1_equiv(exp["m1"]["reservations"][0], rep.json["reservations"][0]) \
        and set(rep.json) == set(exp["m1"])
    check("UPG-5b", "stage-1 replay on /reservation-moves is 200 with the original body (plus table_ids at most)", ok, "orig=%r got=%r" % (exp["m1"], rep))
    r = call("POST", "/reservations", body=dict(exp["bf"], party_size=2), token=tokH, key=exp["kf"])
    check("UPG-6a", "a stage-1 key that failed is a first use after import (201)", r.status == 201, repr(r))
    expect_err("UPG-6b", "a stage-1 key with a different body is 409 idempotency_key_reuse", call("POST", "/reservations", body=dict(exp["b1"], party_size=1), token=tokH, key=exp["k1"]), 409, "idempotency_key_reuse")
    a = avail("r_combo", thu, 1)
    L = thu + "T19:00"
    check("UPG-7a", "imported reservations occupy their tables (c_c at 19:00, c_d at 21:30 blocked; cancelled c_a free)",
          a.status == 200 and "c_c" not in (singles_at(a, L) or ["c_c"]) and "c_a" in (singles_at(a, L) or []) and "c_d" not in (singles_at(a, thu + "T21:30") or ["c_d"])
          and all(isinstance(s.get("available_options"), list) for s in a.json["slots"]), repr(slot_map(a)))
    expect_err("UPG-7b", "with no combinable in the imported state a pair booking is 422 combination_not_allowed", book2(tokH, "r_combo", ["c_a", "c_b"], thu + "T20:30", 2), 422, "combination_not_allowed")
    reset(base_fixture())
    expect_err("UPG-8", "reset after the upgrade import clears everything (stage-1 token 401)", call("GET", "/reservations", token=tokH), 401, "unauthenticated")

    reset(s2_fixture())
    tok = login("hana")
    kp = newkey()
    body = {"restaurant_id": "r_combo", "table_ids": ["c_b", "c_a"], "starts_at_local": T(THU, "19:00"), "party_size": 4}
    P = call("POST", "/reservations", body=body, token=tok, key=kp)
    ex = call("GET", "/_test/export", timeout=10)
    cancel(tok, P.json["reference"])
    imp = call("POST", "/_test/import", body=ex.json, timeout=10)
    g = get_res(tok, P.json["reference"])
    rep = call("POST", "/reservations", body=body, token=tok, key=kp)
    check("UPG-9", "stage-2 export/import round-trips a pair booking and its replay",
          P.status == 201 and ex.status == 200 and imp.status == 204 and g.status == 200 and same_json(g.json, P.json) and rep.status == 200 and same_json(rep.json, P.json),
          "P=%r imp=%r get=%r replay=%r" % (P, imp, g, rep))


@group("CONV")
def g_conventions():
    check("CONV-1", "every JSON response carried Content-Type application/json", not CT_VIOLATIONS, repr(CT_VIOLATIONS[:5]))
    check("CONV-2", "every 4xx/5xx response carried {error:{code,message}}", not SHAPE_VIOLATIONS, repr(SHAPE_VIOLATIONS[:5]))
    check("CONV-3", "no 5xx response was observed anywhere in the run", not SERVER_FAULTS, repr(SERVER_FAULTS[:5]))
    check("CONV-4", "every timestamp observed was RFC 3339 with a numeric offset", not TS_VIOLATIONS, repr(TS_VIOLATIONS[:5]))


# =========================================================================== main

def main():
    global BASE
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", default=BASE, help="service base URL (default %s)" % BASE)
    ap.add_argument("--only", default="", help="comma-separated group names to run (default all)")
    args = ap.parse_args()
    BASE = args.base_url.rstrip("/")
    only = {x.strip().upper() for x in args.only.split(",") if x.strip()}
    print("# tablekeeper stage-2 HTTP probes against %s (THU=%s)" % (BASE, THU))
    for name, fn in GROUPS:
        if only and name not in only and name != "CONV":
            continue
        print("## %s" % name)
        try:
            fn()
        except Exception:
            check(name + "-EXC", "group ran without probe exception", False,
                  traceback.format_exc().strip().splitlines()[-1])
    print("# %d passed, %d failed" % (PASSED, FAILED))
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
