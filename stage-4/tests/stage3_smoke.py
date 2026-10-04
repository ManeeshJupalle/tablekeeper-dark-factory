"""Stage-3 HTTP checks for Tablekeeper, run from tests/smoke.py.

Covers dated policies and accepted terms, revisions and stale_revision, history
and decision, availability explanations, recurring series, collective moves
under policies, the restaurant revision, export/import round trips, upgrades
from real stage-1 and stage-2 exports, and the concurrency invariants.
Standard library only; shares smoke.py's client, counters and fixtures.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import urllib.request

s = None  # the running smoke module, set by run()

OFFSET_TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$")
HUGE_CUTOFF = 60 * 24 * 3650


def d(n: int) -> str:
    """The smoke date plus n days."""
    return (dt.date.fromisoformat(s.DATE) + dt.timedelta(days=n)).isoformat()


def weekday(date: str) -> str:
    return s.WEEKDAYS[dt.date.fromisoformat(date).weekday()]


def managed(**over):
    base = dict(manager_user_ids=["u_ada"], combinable=[["t_1", "t_2"], ["t_2", "t_3"]])
    base.update(over)
    return s.restaurant(**base)


def policy(date, **over):
    p = dict(effective_from=date, slot_minutes=30, reservation_duration_minutes=90,
             cancellation_cutoff_minutes=120, opening_hours=s.all_week(),
             capacities={"t_1": 2, "t_2": 4, "t_3": 6})
    p.update(over)
    return p


def publish(token, body, key=None, rid="r_anker"):
    return s.call("POST", f"/restaurants/{rid}/policies", body=body, token=token, key=key or s.new_key())


def detail_rev(rid="r_anker"):
    return s.call("GET", f"/restaurants/{rid}").json()["revision"]


def book(token, table="t_2", at="19:00", party=4, date=None, key=None, table_ids=None, rid="r_anker"):
    body = {"restaurant_id": rid, "starts_at_local": f"{date or s.DATE}T{at}", "party_size": party}
    if table_ids:
        body["table_ids"] = table_ids
    else:
        body["table_id"] = table
    return s.call("POST", "/reservations", body=body, token=token, key=key or s.new_key())


def patch(token, ref, body):
    return s.call("PATCH", f"/reservations/{ref}", body=body, token=token)


def get(token, ref):
    return s.call("GET", f"/reservations/{ref}", token=token).json()


def history(token, ref):
    return s.call("GET", f"/reservations/{ref}/history", token=token).json()["entries"]


def series(token, body, key=None):
    return s.call("POST", "/series", body=body, token=token, key=key or s.new_key())


def moves(token, items, key=None):
    return s.call("POST", "/reservation-moves", body={"moves": items}, token=token, key=key or s.new_key())


def explain(date, party, rid="r_anker"):
    return s.call("GET", f"/availability?restaurant_id={rid}&date={date}&party_size={party}&explain=true").json()


def base_terms():
    return {"policy_version": 0, "slot_minutes": 30, "reservation_duration_minutes": 90,
            "cancellation_cutoff_minutes": 120, "opening_hours": s.all_week(),
            "capacities": {"t_1": 2, "t_2": 4, "t_3": 6}}


def fresh(restaurants=None, users=None, reservations=None):
    s.expect("s3 reset", s.reset(s.fixture(users=users, restaurants=restaurants or [managed()], reservations=reservations)), 204)
    return s.login(s.ADA["email"], s.ADA["password"]), s.login(s.BOB["email"], s.BOB["password"])


# ---- policies -------------------------------------------------------------------

def test_policies():
    ada, bob = fresh()
    check, expect = s.check, s.expect
    check("pol restaurant revision starts at 1", detail_rev() == 1)
    expect("pol no token", s.call("POST", "/restaurants/r_anker/policies", body=policy(d(0)), key=s.new_key()), 401, "unauthenticated")
    expect("pol non-manager", publish(bob, policy(d(0))), 403, "forbidden")
    expect("pol unknown restaurant", publish(ada, policy(d(0)), rid="r_nope"), 404, "not_found")
    expect("pol missing key", s.call("POST", "/restaurants/r_anker/policies", body=policy(d(0)), token=ada), 400, "missing_idempotency_key")
    expect("pol malformed", s.call("POST", "/restaurants/r_anker/policies", raw=b"{", token=ada, key=s.new_key()), 400, "malformed_request")
    two_mondays = [{"weekday": "mon", "opens": "18:00", "closes": "23:00"}] * 2
    bad = [
        ("effective_from missing", {"effective_from": None}), ("effective_from bad date", {"effective_from": "2026-02-30"}),
        ("effective_from not a date", {"effective_from": "soon"}), ("effective_from number", {"effective_from": 20260101}),
        ("slot 0", {"slot_minutes": 0}), ("slot 1441", {"slot_minutes": 1441}), ("slot bool", {"slot_minutes": True}),
        ("slot string", {"slot_minutes": "30"}), ("slot float", {"slot_minutes": 30.5}), ("slot null", {"slot_minutes": None}),
        ("duration 0", {"reservation_duration_minutes": 0}), ("duration 1441", {"reservation_duration_minutes": 1441}),
        ("cutoff -1", {"cancellation_cutoff_minutes": -1}), ("cutoff 10081", {"cancellation_cutoff_minutes": 10081}),
        ("cutoff bool", {"cancellation_cutoff_minutes": False}),
        ("hours not array", {"opening_hours": "daily"}), ("hours duplicate weekday", {"opening_hours": two_mondays}),
        ("hours bad weekday", {"opening_hours": [{"weekday": "xyz", "opens": "18:00", "closes": "23:00"}]}),
        ("hours closes before opens", {"opening_hours": [{"weekday": "mon", "opens": "23:00", "closes": "18:00"}]}),
        ("caps empty", {"capacities": {}}), ("caps missing table", {"capacities": {"t_1": 2, "t_2": 4}}),
        ("caps extra table", {"capacities": {"t_1": 2, "t_2": 4, "t_3": 6, "t_9": 2}}),
        ("caps 0", {"capacities": {"t_1": 0, "t_2": 4, "t_3": 6}}), ("caps 101", {"capacities": {"t_1": 101, "t_2": 4, "t_3": 6}}),
        ("caps bool", {"capacities": {"t_1": True, "t_2": 4, "t_3": 6}}), ("caps not object", {"capacities": [2, 4, 6]}),
    ]
    for name, over in bad:
        body = policy(d(0), **over)
        body = {k: v for k, v in body.items() if v is not None}
        expect(f"pol invalid {name}", publish(ada, body), 422, "validation_failed")
    for field in ("effective_from", "slot_minutes", "reservation_duration_minutes", "cancellation_cutoff_minutes", "opening_hours", "capacities"):
        body = {k: v for k, v in policy(d(0)).items() if k != field}
        expect(f"pol missing {field}", publish(ada, body), 422, "validation_failed")
    check("pol invalid allocates nothing", s.call("GET", "/restaurants/r_anker/policies").json() == {"policies": []} and detail_rev() == 1)

    key = s.new_key()
    body1 = {**policy(d(0), reservation_duration_minutes=60), "souvenir": "ignored"}
    p1 = expect("pol publish v1", publish(ada, body1, key=key), 201).json()
    check("pol v1 echo", p1 == {**policy(d(0), reservation_duration_minutes=60), "policy_version": 1}, json.dumps(p1))
    check("pol replay", s.expect("pol replay status", publish(ada, body1, key=key), 200).json() == p1)
    expect("pol key reuse", publish(ada, policy(d(0)), key=key), 409, "idempotency_key_reuse")
    check("pol one restaurant revision", detail_rev() == 2)
    p2 = expect("pol publish v2", publish(ada, policy(d(14), slot_minutes=60)), 201).json()
    p3 = expect("pol publish v3 earlier date", publish(ada, policy(d(-30), cancellation_cutoff_minutes=0)), 201).json()
    check("pol versions", [p1["policy_version"], p2["policy_version"], p3["policy_version"]] == [1, 2, 3])
    listed = s.expect("pol list public", s.call("GET", "/restaurants/r_anker/policies"), 200).json()["policies"]
    check("pol list publication order", listed == [p1, p2, p3], json.dumps(listed)[:200])
    expect("pol list unknown", s.call("GET", "/restaurants/r_nope/policies"), 404, "not_found")
    detail = s.call("GET", "/restaurants/r_anker").json()
    check("pol detail keeps fixture", detail["slot_minutes"] == 30 and detail["reservation_duration_minutes"] == 90
          and detail["cancellation_cutoff_minutes"] == 120 and [t["capacity"] for t in detail["tables"]] == [2, 4, 6])

    b0 = expect("pol book before v1 date", book(ada, date=d(-1)), 201).json()
    check("pol earlier date uses past-dated v3", b0["accepted_terms"]["policy_version"] == 3 and b0["accepted_terms"]["cancellation_cutoff_minutes"] == 0)
    b1 = expect("pol book on v1 date", book(ada, date=d(0)), 201).json()
    check("pol v1 terms", b1["accepted_terms"] == {k: v for k, v in p1.items() if k != "effective_from"}, json.dumps(b1["accepted_terms"]))
    check("pol v1 ends_at uses 60", b1["ends_at"].startswith(f"{d(0)}T20:00:00"), b1["ends_at"])
    b2 = expect("pol book on v2 date", book(ada, date=d(14)), 201).json()
    check("pol v2 selected", b2["accepted_terms"]["policy_version"] == 2)
    expect("pol v2 grid", book(ada, date=d(14), at="19:30", table="t_3"), 422, "not_on_slot_grid")
    p4 = expect("pol same-date supersedes", publish(ada, policy(d(0), reservation_duration_minutes=120)), 201).json()
    b4 = expect("pol book after supersede", book(ada, table="t_3", date=d(0)), 201).json()
    check("pol v4 selected", b4["accepted_terms"]["policy_version"] == p4["policy_version"] == 4 and b4["ends_at"].startswith(f"{d(0)}T21:00:00"))
    check("pol publication leaves bookings", get(ada, b1["reference"]) == b1 and len(history(ada, b1["reference"])) == 1)
    expect("pol tie same date", publish(ada, policy(d(0), reservation_duration_minutes=30)), 201)
    b5 = expect("pol book after tie", book(ada, table="t_1", party=2, at="21:00", date=d(0)), 201).json()
    check("pol greatest version wins tie", b5["accepted_terms"]["policy_version"] == 5)
    check("pol explain names policy", all(e["policy_version"] == 5 for slot in explain(d(0), 2)["slots"] for e in slot["explain"]))

    closed = [h for h in s.all_week() if h["weekday"] != weekday(d(21))]
    expect("pol closing weekday", publish(ada, policy(d(21), opening_hours=closed)), 201)
    check("pol closed day no slots", s.availability("r_anker", d(21), 2).json()["slots"] == [])
    expect("pol closed day booking", book(ada, date=d(21)), 422, "outside_opening_hours")
    expect("pol capacities policy", publish(ada, policy(d(28), capacities={"t_1": 2, "t_2": 2, "t_3": 6})), 201)
    expect("pol policy capacity refuses", book(ada, date=d(28)), 422, "party_exceeds_capacity")
    slot = next(x for x in explain(d(28), 4)["slots"] if x["starts_at_local"].endswith("19:00"))
    t2 = next(e for e in slot["explain"] if e["table_id"] == "t_2")
    check("pol explain capacity from policy", t2["rules"][0] == {"rule": "capacity", "holds": False})
    check("pol pair capacity from policy", {"table_ids": ["t_1", "t_2"], "capacity": 4} in slot["available_options"], json.dumps(slot["available_options"]))
    expect("pol invalid after", publish(ada, policy(d(0), slot_minutes=0)), 422, "validation_failed")
    p8 = expect("pol next version after failure", publish(ada, policy(d(35))), 201).json()
    check("pol failures allocate no version", p8["policy_version"] == 8)
    old_key = s.new_key()
    old = expect("pol receipt before policy", book(ada, date=d(42), key=old_key), 201).json()
    expect("pol publish over receipt", publish(ada, policy(d(42), reservation_duration_minutes=45)), 201)
    check("pol old receipt unchanged", s.expect("pol old replay", book(ada, date=d(42), key=old_key), 200).json() == old)


def test_own_duration_overlap():
    ada, _ = fresh()
    b = s.expect("own book 90", book(ada), 201).json()
    s.expect("own shorter policy", publish(ada, policy(s.DATE, reservation_duration_minutes=30)), 201)
    by_time = {x["starts_at_local"][11:]: x for x in explain(s.DATE, 4)["slots"]}
    t2 = lambda hhmm: next(e for e in by_time[hhmm]["explain"] if e["table_id"] == "t_2")
    s.check("own 20:00 still occupied by 90-min booking", t2("20:00")["rules"] == [{"rule": "capacity", "holds": True}, {"rule": "no_overlap", "holds": False}])
    s.check("own 20:30 free", t2("20:30")["available"] is True and "t_2" in by_time["20:30"]["available_table_ids"])
    s.expect("own 20:00 refused", book(ada, at="20:00"), 409, "table_unavailable")
    s.expect("own 20:30 booked", book(ada, at="20:30"), 201)
    s.check("own original untouched", get(ada, b["reference"]) == b)


# ---- revisions, history, decision -------------------------------------------------

def test_revisions_and_history():
    ada, bob = fresh()
    check, expect = s.check, s.expect
    b = expect("rev book", book(ada, table="t_1", party=2), 201).json()
    ref = b["reference"]
    check("rev 1 and policy-0 terms", b["revision"] == 1 and b["accepted_terms"] == base_terms(), json.dumps(b["accepted_terms"]))
    check("rev decision", s.call("GET", f"/reservations/{ref}/decision", token=ada).json() == {"reference": ref, "revision": 1, "accepted_terms": b["accepted_terms"]})
    h = history(ada, ref)
    check("hist created", len(h) == 1 and h[0]["seq"] == 1 and h[0]["event"] == "created" and h[0]["revision"] == 1
          and h[0]["accepted_terms"] == base_terms() and h[0]["at"] == b["created_at"] and OFFSET_TS.match(h[0]["at"]), json.dumps(h))
    check("hist created changes", h[0]["changes"] == [
        {"field": "table_id", "from": None, "to": "t_1"},
        {"field": "starts_at_local", "from": None, "to": f"{s.DATE}T19:00"},
        {"field": "party_size", "from": None, "to": 2}], json.dumps(h[0]["changes"]))
    check("hist envelope", s.call("GET", f"/reservations/{ref}/history", token=ada).json()["reference"] == ref)
    for name, value in (("zero", 0), ("negative", -1), ("bool", True), ("string", "1"), ("float", 1.5), ("null", None)):
        expect(f"rev expected_revision {name}", patch(ada, ref, {"party_size": 1, "expected_revision": value}), 422, "validation_failed")
    expect("rev stale", patch(ada, ref, {"party_size": 1, "expected_revision": 2}), 409, "stale_revision")
    check("rev failures change nothing", get(ada, ref) == b and len(history(ada, ref)) == 1)
    r = expect("rev no-op", patch(ada, ref, {"table_id": "t_1", "party_size": 2, "starts_at_local": f"{s.DATE}T19:00"}), 200).json()
    check("rev no-op keeps revision", r == b and len(history(ada, ref)) == 1)
    expect("rev empty patch", patch(ada, ref, {}), 200)
    r = expect("rev real change", patch(ada, ref, {"table_id": "t_2", "expected_revision": 1}), 200).json()
    check("rev 2", r["revision"] == 2)
    r = expect("rev two fields", patch(ada, ref, {"starts_at_local": f"{s.DATE}T20:00", "party_size": 3}), 200).json()
    r = expect("rev to pair", patch(ada, ref, {"table_ids": ["t_2", "t_1"]}), 200).json()
    check("rev pair normalised", r["table_ids"] == ["t_1", "t_2"] and "table_id" not in r and r["revision"] == 4)
    expect("rev reversed pair is no-op", patch(ada, ref, {"table_ids": ["t_2", "t_1"]}), 200)
    r = expect("rev pair to single", patch(ada, ref, {"table_id": "t_3"}), 200).json()
    r = expect("rev single to single", patch(ada, ref, {"table_id": "t_2"}), 200).json()
    expect("rev unknown table", patch(ada, ref, {"table_id": "t_9"}), 404, "not_found")
    expect("rev off grid", patch(ada, ref, {"starts_at_local": f"{s.DATE}T19:15"}), 422, "not_on_slot_grid")
    expect("bob books 21:00", book(bob, table="t_3", at="21:00", party=2), 201)
    expect("rev occupied", patch(ada, ref, {"table_id": "t_3"}), 409, "table_unavailable")
    check("rev failed amendments change nothing", get(ada, ref) == r)
    h = history(ada, ref)
    check("hist seq contiguous", [e["seq"] for e in h] == list(range(1, len(h) + 1)) == [1, 2, 3, 4, 5, 6])
    check("hist revisions", [e["revision"] for e in h] == [1, 2, 3, 4, 5, 6])
    check("hist at ordered", [e["at"] for e in h] == sorted(e["at"] for e in h) and all(OFFSET_TS.match(e["at"]) for e in h))
    check("hist changes", [e["changes"] for e in h[1:]] == [
        [{"field": "table_id", "from": "t_1", "to": "t_2"}],
        [{"field": "starts_at_local", "from": f"{s.DATE}T19:00", "to": f"{s.DATE}T20:00"}, {"field": "party_size", "from": 2, "to": 3}],
        [{"field": "table_ids", "from": ["t_2"], "to": ["t_1", "t_2"]}],
        [{"field": "table_ids", "from": ["t_1", "t_2"], "to": ["t_3"]}],
        [{"field": "table_id", "from": "t_3", "to": "t_2"}]], json.dumps([e["changes"] for e in h[1:]]))
    for who, tok in (("bob", bob), ("no token", None), ("bad token", "nope")):
        expect(f"hist {who} 404", s.call("GET", f"/reservations/{ref}/history", token=tok), 404, "not_found")
        expect(f"decision {who} 404", s.call("GET", f"/reservations/{ref}/decision", token=tok), 404, "not_found")
    expect("hist unknown 404", s.call("GET", "/reservations/NOPE1234/history", token=ada), 404, "not_found")
    c = expect("rev cancel", s.call("POST", f"/reservations/{ref}/cancel", token=ada), 200).json()
    check("rev cancel +1", c["revision"] == 7 and c["status"] == "cancelled")
    c2 = expect("rev cancel again", s.call("POST", f"/reservations/{ref}/cancel", token=ada), 200).json()
    h = history(ada, ref)
    check("hist cancelled last", c2 == c and len(h) == 7 and h[-1]["event"] == "cancelled" and h[-1]["changes"] == [] and h[-1]["revision"] == 7)
    check("decision after cancel", s.call("GET", f"/reservations/{ref}/decision", token=ada).json()["revision"] == 7)
    expect("rev patch cancelled", patch(ada, ref, {"party_size": 1}), 409, "reservation_cancelled")
    pair = expect("hist pair book", book(ada, table_ids=["t_2", "t_1"], party=5, at="21:30"), 201).json()
    check("hist pair created", history(ada, pair["reference"])[0]["changes"][0] == {"field": "table_ids", "from": None, "to": ["t_1", "t_2"]})
    # amendment adopts the resulting date's policy; old entries keep their terms
    s.expect("rev publish later policy", publish(ada, policy(d(7), reservation_duration_minutes=60)), 201)
    m = expect("rev book to move", book(ada, table="t_3", party=2, at="18:00"), 201).json()
    moved = expect("rev move to new policy date", patch(ada, m["reference"], {"starts_at_local": f"{d(7)}T19:00"}), 200).json()
    h = history(ada, m["reference"])
    check("rev adopts new terms", moved["accepted_terms"]["policy_version"] == 1 and moved["ends_at"].startswith(f"{d(7)}T20:00:00"))
    check("hist old entry keeps old terms", h[0]["accepted_terms"]["policy_version"] == 0 and h[1]["accepted_terms"]["policy_version"] == 1)


def test_accepted_cutoff():
    ada, _ = fresh(restaurants=[managed(cancellation_cutoff_minutes=HUGE_CUTOFF)])
    old = s.expect("cut old booking", book(ada), 201).json()
    s.expect("cut publish cutoff 0", publish(ada, policy(s.DATE, cancellation_cutoff_minutes=0)), 201)
    new = s.expect("cut new booking", book(ada, table="t_3"), 201).json()
    s.check("cut new terms", new["accepted_terms"]["cancellation_cutoff_minutes"] == 0)
    s.expect("cut stale before cutoff", patch(ada, old["reference"], {"party_size": 2, "expected_revision": 9}), 409, "stale_revision")
    s.expect("cut old accepted cutoff applies", patch(ada, old["reference"], {"party_size": 2}), 409, "cutoff_passed")
    s.expect("cut old cancel refused", s.call("POST", f"/reservations/{old['reference']}/cancel", token=ada), 409, "cutoff_passed")
    s.expect("cut new patch ok", patch(ada, new["reference"], {"party_size": 3}), 200)
    s.expect("cut new cancel ok", s.call("POST", f"/reservations/{new['reference']}/cancel", token=ada), 200)
    s.check("cut old untouched", get(ada, old["reference"]) == old)


def test_concurrent_amendments():
    ada, _ = fresh()
    b = s.expect("cc book", book(ada, table="t_3", party=2), 201).json()
    out = s.burst(lambda i: patch(ada, b["reference"], {"party_size": 3 + i % 4, "expected_revision": 1}), 50)
    counts = s.tally(out)
    s.check("cc one real change wins", counts.get(200) == 1 and counts.get(409) == 49, str(counts))
    s.check("cc losers stale", {r.code() for r in out if r.status == 409} == {"stale_revision"})
    s.check("cc one history entry", get(ada, b["reference"])["revision"] == 2 and len(history(ada, b["reference"])) == 2)


# ---- explanations -----------------------------------------------------------------

def test_explain():
    ada, _ = fresh()
    s.expect("ex booking", book(ada), 201)
    for value in ("false", "1", "", "TRUE", "yes"):
        s.expect(f"ex invalid {value!r}", s.call("GET", f"/availability?restaurant_id=r_anker&date={s.DATE}&party_size=4&explain={value}"), 422, "validation_failed")
    s.expect("ex bare flag", s.call("GET", f"/availability?restaurant_id=r_anker&date={s.DATE}&party_size=4&explain"), 422, "validation_failed")
    plain = s.availability("r_anker", s.DATE, 4).json()
    s.check("ex absent keeps shape", all("explain" not in slot for slot in plain["slots"]))
    for party in (2, 4, 7):
        body = explain(s.DATE, party)
        ok = True
        for slot in body["slots"]:
            ids = [e["table_id"] for e in slot["explain"]]
            ok &= ids == ["t_1", "t_2", "t_3"]
            for e in slot["explain"]:
                ok &= [r["rule"] for r in e["rules"]] == ["capacity", "no_overlap"] and e["policy_version"] == 0
                ok &= e["available"] == all(r["holds"] for r in e["rules"])
            ok &= [e["table_id"] for e in slot["explain"] if e["available"]] == slot["available_table_ids"]
        s.check(f"ex consistent party {party}", ok and len(body["slots"]) == 8)
    seven = next(x for x in explain(s.DATE, 7)["slots"] if x["starts_at_local"].endswith("19:00"))
    t2 = next(e for e in seven["explain"] if e["table_id"] == "t_2")
    s.check("ex both rules false reported", t2["rules"] == [{"rule": "capacity", "holds": False}, {"rule": "no_overlap", "holds": False}] and t2["available"] is False)
    s.check("ex empty slot still explained", seven["available_table_ids"] == [] and len(seven["explain"]) == 3)
    closed = [h for h in s.all_week() if h["weekday"] != weekday(s.DATE)]
    fresh(restaurants=[managed(opening_hours=closed)])
    s.check("ex closed day", explain(s.DATE, 2)["slots"] == [])


# ---- series ------------------------------------------------------------------------

def test_series():
    ada, bob = fresh()
    check, expect = s.check, s.expect
    anchor = expect("ser anchor", book(ada), 201).json()
    aref = anchor["reference"]
    body = {"anchor_reference": aref, "count": 3, "interval_weeks": 1}
    for name, over in (("count 1", {"count": 1}), ("count 13", {"count": 13}), ("count bool", {"count": True}),
                       ("count string", {"count": "3"}), ("count float", {"count": 2.5}), ("weeks 0", {"interval_weeks": 0}),
                       ("weeks 5", {"interval_weeks": 5}), ("weeks bool", {"interval_weeks": True})):
        expect(f"ser invalid {name}", series(ada, {**body, **over}), 422, "validation_failed")
    for field in ("anchor_reference", "count", "interval_weeks"):
        expect(f"ser missing {field}", series(ada, {k: v for k, v in body.items() if k != field}), 422, "validation_failed")
    expect("ser missing key", s.call("POST", "/series", body=body, token=ada), 400, "missing_idempotency_key")
    expect("ser no token", s.call("POST", "/series", body=body, key=s.new_key()), 401, "unauthenticated")
    expect("ser unknown anchor", series(ada, {**body, "anchor_reference": "NOPE1234"}), 404, "not_found")
    expect("ser foreign anchor", series(bob, body), 404, "not_found")
    rev = detail_rev()
    key = s.new_key()
    sr = expect("ser create", series(ada, body, key=key), 201).json()
    occ = sr["occurrences"]
    check("ser shape", sr["revision"] == 1 and sr["interval_weeks"] == 1 and [o["index"] for o in occ] == [0, 1, 2]
          and all(o["exception"] is False for o in occ) and len({o["reference"] for o in occ}) == 3)
    check("ser anchor unchanged", occ[0]["reference"] == aref and occ[0]["reservation"] == anchor and get(ada, aref) == anchor)
    check("ser dates", [o["reservation"]["starts_at_local"] for o in occ] == [f"{s.DATE}T19:00", f"{d(7)}T19:00", f"{d(14)}T19:00"])
    check("ser occurrence fields", all(o["reservation"]["table_ids"] == ["t_2"] and o["reservation"]["party_size"] == 4 and o["reservation"]["revision"] == 1 for o in occ))
    check("ser restaurant revision once", detail_rev() == rev + 1)
    check("ser replay", expect("ser replay status", series(ada, body, key=key), 200).json() == sr)
    expect("ser key reuse", series(ada, {**body, "count": 2}, key=key), 409, "idempotency_key_reuse")
    expect("ser already adopted", series(ada, body), 409, "already_in_series")
    expect("ser occurrence already adopted", series(ada, {**body, "anchor_reference": occ[1]["reference"]}), 409, "already_in_series")
    mine = {r["reference"] for r in s.call("GET", "/reservations", token=ada).json()["reservations"]}
    check("ser occurrences listed", {o["reference"] for o in occ} <= mine)
    check("ser occurrence history", [e["event"] for e in history(ada, occ[1]["reference"])] == ["created"])
    check("ser occurrence occupies", "t_2" not in s.slot_tables("r_anker", d(7), 4, "19:00"))
    check("ser get", s.call("GET", f"/series/{sr['series_id']}", token=ada).json() == sr)
    for who, tok in (("bob", bob), ("no token", None)):
        expect(f"ser get {who} 404", s.call("GET", f"/series/{sr['series_id']}", token=tok), 404, "not_found")
    expect("ser get unknown", s.call("GET", "/series/ser_nope", token=ada), 404, "not_found")
    expect("ser patch occurrence", patch(ada, occ[1]["reference"], {"party_size": 3}), 200)
    g = s.call("GET", f"/series/{sr['series_id']}", token=ada).json()
    check("ser exception and revision", g["revision"] == 2 and [o["exception"] for o in g["occurrences"]] == [False, True, False])
    expect("ser no-op patch", patch(ada, occ[2]["reference"], {"party_size": 4}), 200)
    expect("ser failed patch", patch(ada, occ[2]["reference"], {"starts_at_local": f"{d(14)}T19:15"}), 422, "not_on_slot_grid")
    check("ser no-op/failure no revision", s.call("GET", f"/series/{sr['series_id']}", token=ada).json()["revision"] == 2)
    expect("ser cancel occurrence", s.call("POST", f"/reservations/{occ[2]['reference']}/cancel", token=ada), 200)
    expect("ser cancel again", s.call("POST", f"/reservations/{occ[2]['reference']}/cancel", token=ada), 200)
    g = s.call("GET", f"/series/{sr['series_id']}", token=ada).json()
    check("ser cancel revision once, no exception", g["revision"] == 3 and g["occurrences"][2]["exception"] is False
          and g["occurrences"][2]["reservation"]["status"] == "cancelled")
    expect("ser cancel anchor", s.call("POST", f"/reservations/{aref}/cancel", token=ada), 200)
    g = s.call("GET", f"/series/{sr['series_id']}", token=ada).json()
    check("ser anchor cancel keeps siblings", g["revision"] == 4 and g["occurrences"][1]["reservation"]["status"] == "confirmed")
    check("ser replay after changes", series(ada, body, key=key).json() == sr)
    expect("ser cancelled anchor", series(ada, {**body, "anchor_reference": aref}), 409, "reservation_cancelled")

    # all-or-nothing: the first failing occurrence decides; nothing survives
    a2 = expect("ser anchor 2", book(ada, table="t_3", party=2), 201).json()
    expect("ser bob blocks week 2", book(bob, table="t_3", party=2, date=d(14)), 201)
    before = len(s.call("GET", "/reservations", token=ada).json()["reservations"])
    rev = detail_rev()
    k2 = s.new_key()
    expect("ser occupancy fails", series(ada, {"anchor_reference": a2["reference"], "count": 3, "interval_weeks": 1}, key=k2), 409, "table_unavailable")
    check("ser nothing partial", len(s.call("GET", "/reservations", token=ada).json()["reservations"]) == before and detail_rev() == rev
          and get(ada, a2["reference"]) == a2 and len(history(ada, a2["reference"])) == 1)
    expect("ser failed key reusable", series(ada, {"anchor_reference": a2["reference"], "count": 2, "interval_weeks": 1}, key=k2), 201)

    a3 = expect("ser anchor 3", book(ada, table="t_1", party=2), 201).json()
    expect("ser close week 1", publish(ada, policy(d(7), opening_hours=[h for h in s.all_week() if h["weekday"] != weekday(d(7))])), 201)
    expect("ser reopen after", publish(ada, policy(d(8))), 201)
    expect("ser bob blocks t_1 week 2", book(bob, table="t_1", party=2, date=d(14)), 201)
    expect("ser first failing index wins", series(ada, {"anchor_reference": a3["reference"], "count": 3, "interval_weeks": 1}), 422, "outside_opening_hours")
    expect("ser shorter policy week 3", publish(ada, policy(d(21), reservation_duration_minutes=60)), 201)
    a4 = expect("ser anchor 4", book(ada, table="t_3", party=2, at="21:30"), 201).json()
    sr4 = expect("ser per-date policy", series(ada, {"anchor_reference": a4["reference"], "count": 2, "interval_weeks": 3}), 201).json()
    o1 = sr4["occurrences"][1]["reservation"]
    check("ser occurrence adopts its date's policy", o1["starts_at_local"] == f"{d(21)}T21:30" and o1["accepted_terms"]["policy_version"] == 3
          and o1["ends_at"].startswith(f"{d(21)}T22:30:00") and sr4["occurrences"][0]["reservation"]["accepted_terms"]["policy_version"] == 0)

    # cutoff and DST
    ada, _ = fresh(restaurants=[managed(cancellation_cutoff_minutes=HUGE_CUTOFF)])
    a = expect("ser cutoff anchor", book(ada), 201).json()
    expect("ser cutoff refused", series(ada, {"anchor_reference": a["reference"], "count": 2, "interval_weeks": 1}), 409, "cutoff_passed")
    ada, _ = fresh(restaurants=[managed(opening_hours=s.all_week("00:00", "23:30"))])
    a = expect("ser dst anchor", book(ada, at="02:30", date="2027-03-21"), 201).json()
    expect("ser nonexistent local time", series(ada, {"anchor_reference": a["reference"], "count": 2, "interval_weeks": 1}), 422, "invalid_local_time")
    check("ser dst nothing created", len(s.call("GET", "/reservations", token=ada).json()["reservations"]) == 1)
    a = expect("ser fall-back anchor", book(ada, table="t_3", at="02:30", date="2027-10-24"), 201).json()
    srf = expect("ser repeated time", series(ada, {"anchor_reference": a["reference"], "count": 2, "interval_weeks": 1}), 201).json()
    check("ser repeated time first occurrence", srf["occurrences"][1]["reservation"]["starts_at"] == "2027-10-31T02:30:00+02:00",
          srf["occurrences"][1]["reservation"]["starts_at"])


# ---- moves under policies ------------------------------------------------------------

def test_moves():
    ada, _ = fresh()
    check, expect = s.check, s.expect
    b1 = expect("mv b1", book(ada, table="t_1", party=2), 201).json()
    b2 = expect("mv b2", book(ada, table="t_2", party=2), 201).json()
    b3 = expect("mv b3", book(ada, table="t_3", party=2, at="21:00"), 201).json()
    rev = detail_rev()
    expect("mv stale", moves(ada, [{"reference": b1["reference"], "table_id": "t_2", "expected_revision": 3}]), 409, "stale_revision")
    expect("mv invalid expected", moves(ada, [{"reference": b1["reference"], "expected_revision": 0}]), 422, "validation_failed")
    expect("mv occupancy", moves(ada, [{"reference": b1["reference"], "table_id": "t_3", "starts_at_local": f"{s.DATE}T20:00"}]), 409, "table_unavailable")
    check("mv failures change nothing", [get(ada, b["reference"]) for b in (b1, b2, b3)] == [b1, b2, b3] and detail_rev() == rev)
    key = s.new_key()
    items = [{"reference": b1["reference"], "table_id": "t_2", "expected_revision": 1},
             {"reference": b2["reference"], "table_id": "t_1"},
             {"reference": b3["reference"], "table_id": "t_3", "party_size": 2}]
    r = expect("mv swap with no-op", moves(ada, items, key=key), 201).json()["reservations"]
    check("mv revisions", [x["revision"] for x in r] == [2, 2, 1] and r[2] == b3)
    check("mv histories", [len(history(ada, b["reference"])) for b in (b1, b2, b3)] == [2, 2, 1])
    check("mv restaurant revision once", detail_rev() == rev + 1)
    check("mv replay", expect("mv replay status", moves(ada, items, key=key), 200).json()["reservations"] == r and detail_rev() == rev + 1)
    expect("mv publish", publish(ada, policy(d(7), reservation_duration_minutes=60)), 201)
    r = expect("mv adopts date policy", moves(ada, [{"reference": b3["reference"], "starts_at_local": f"{d(7)}T19:00"}]), 201).json()["reservations"]
    check("mv new terms", r[0]["accepted_terms"]["policy_version"] == 1 and r[0]["ends_at"].startswith(f"{d(7)}T20:00:00"))
    # two occurrences of one series in one batch: series revision once, both exceptions
    sr = expect("mv series", series(ada, {"anchor_reference": b1["reference"], "count": 3, "interval_weeks": 1}), 201).json()
    o = sr["occurrences"]
    r = expect("mv series batch", moves(ada, [{"reference": o[1]["reference"], "party_size": 1},
                                             {"reference": o[2]["reference"], "party_size": 1}]), 201)
    g = s.call("GET", f"/series/{sr['series_id']}", token=ada).json()
    check("mv series revision once", g["revision"] == 2 and [x["exception"] for x in g["occurrences"]] == [False, True, True])


def test_restaurant_revision():
    ada, _ = fresh()
    steps = []
    k = s.new_key()
    b = book(ada, key=k).json(); steps.append(("create", 2))
    book(ada, key=k); steps.append(("replay", 2))
    book(ada); steps.append(("failed create", 2))
    patch(ada, b["reference"], {"party_size": 3}); steps.append(("patch", 3))
    patch(ada, b["reference"], {"party_size": 3}); steps.append(("no-op patch", 3))
    s.call("POST", f"/reservations/{b['reference']}/cancel", token=ada); steps.append(("cancel", 4))
    s.call("POST", f"/reservations/{b['reference']}/cancel", token=ada); steps.append(("cancel again", 4))
    publish(ada, policy(d(30))); steps.append(("publish", 5))
    publish(ada, policy(d(30), slot_minutes=0)); steps.append(("failed publish", 5))
    a = book(ada, table="t_3", party=2).json(); steps.append(("create 2", 6))
    series(ada, {"anchor_reference": a["reference"], "count": 2, "interval_weeks": 1}); steps.append(("series", 7))
    mk = s.new_key()
    moves(ada, [{"reference": a["reference"], "party_size": 1}], key=mk); steps.append(("batch", 8))
    moves(ada, [{"reference": a["reference"], "party_size": 1}], key=mk); steps.append(("batch replay", 8))
    rev = detail_rev()
    s.check("restaurant revision sequence", rev == 8, f"got {rev}; expected after {steps[-1][0]}")


# ---- export / import --------------------------------------------------------------

def remote(base):
    def call(method, path, body=None, token=None, key=None):
        hdrs = {"Content-Type": "application/json"}
        if token:
            hdrs["Authorization"] = f"Bearer {token}"
        if key:
            hdrs["Idempotency-Key"] = key
        req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None, method=method, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"null")
    return call


def test_upgrade(label, base, with_pairs):
    src = remote(base)
    rest = s.restaurant(combinable=[["t_1", "t_2"]]) if with_pairs else s.restaurant()
    assert src("POST", "/_test/reset", s.fixture(restaurants=[rest]))[0] == 204
    tok = src("POST", "/auth/login", {"email": s.ADA["email"], "password": s.ADA["password"]})[1]["token"]
    k1, kf = s.new_key(), s.new_key()
    body1 = {"restaurant_id": "r_anker", "table_id": "t_2", "starts_at_local": f"{s.DATE}T19:00", "party_size": 4}
    st, r1 = src("POST", "/reservations", body1, tok, k1)
    st2, r2 = src("POST", "/reservations", {**body1, "table_id": "t_3", "starts_at_local": f"{s.DATE}T21:00"}, tok, s.new_key())
    src("POST", f"/reservations/{r2['reference']}/cancel", None, tok)
    failed_body = {**body1}
    stf, _ = src("POST", "/reservations", failed_body, tok, kf)
    pair = None
    if with_pairs:
        _, pair = src("POST", "/reservations", {"restaurant_id": "r_anker", "table_ids": ["t_1", "t_2"], "starts_at_local": f"{d(1)}T19:00", "party_size": 5}, tok, s.new_key())
    snap = src("GET", "/_test/export")[1]
    s.check(f"up {label} source ok", st == 201 and st2 == 201 and stf == 409)
    s.reset(s.fixture(restaurants=[s.restaurant("r_other")]))
    s.expect(f"up {label} import", s.call("POST", "/_test/import", body=snap), 204)
    s.expect(f"up {label} token", s.call("GET", "/reservations", token=tok), 200)
    detail = s.call("GET", "/restaurants/r_anker").json()
    s.check(f"up {label} restaurant", detail["revision"] == 1 and s.call("GET", "/restaurants/r_anker/policies").json() == {"policies": []})
    g = get(tok, r1["reference"])
    s.check(f"up {label} reservation", g["revision"] == 1 and g["accepted_terms"] == base_terms() and g["reservation_id"] == r1["reservation_id"]
            and g["created_at"] == r1["created_at"] and g["table_ids"] == ["t_2"], json.dumps(g))
    h = history(tok, r1["reference"])
    s.check(f"up {label} created history", len(h) == 1 and h[0]["event"] == "created" and h[0]["at"] == r1["created_at"] and h[0]["revision"] == 1)
    s.check(f"up {label} cancelled kept", get(tok, r2["reference"])["status"] == "cancelled")
    s.check(f"up {label} decision", s.call("GET", f"/reservations/{r1['reference']}/decision", token=tok).json()["accepted_terms"]["policy_version"] == 0)
    rep = s.expect(f"up {label} old receipt", s.call("POST", "/reservations", body=body1, token=tok, key=k1), 200).json()
    s.check(f"up {label} receipt verbatim", rep == r1 and "revision" not in rep)
    s.expect(f"up {label} failed key reusable", s.call("POST", "/reservations", body={**body1, "table_id": "t_1", "party_size": 2}, token=tok, key=kf), 201)
    if pair:
        s.check(f"up {label} pair", get(tok, pair["reference"])["table_ids"] == ["t_1", "t_2"]
                and history(tok, pair["reference"])[0]["changes"][0] == {"field": "table_ids", "from": None, "to": ["t_1", "t_2"]})
    sr = s.expect(f"up {label} adopt imported", series(tok, {"anchor_reference": r1["reference"], "count": 2, "interval_weeks": 2}), 201).json()
    s.check(f"up {label} adopted anchor unchanged", sr["occurrences"][0]["reservation"] == g)
    s.expect(f"up {label} import again", s.call("POST", "/_test/import", body=snap), 204)
    s.check(f"up {label} no duplicates", len(s.call("GET", "/reservations", token=tok).json()["reservations"]) == (3 if pair else 2))


def test_roundtrip():
    ada, bob = fresh()
    pk = s.new_key()
    pol_body = policy(d(7), reservation_duration_minutes=60)
    p1 = publish(ada, pol_body, key=pk).json()
    bk = s.new_key()
    b = book(ada, key=bk).json()
    patch(ada, b["reference"], {"party_size": 3})
    sk = s.new_key()
    ser_body = {"anchor_reference": b["reference"], "count": 3, "interval_weeks": 1}
    sr = series(ada, ser_body, key=sk).json()
    patch(ada, sr["occurrences"][1]["reference"], {"party_size": 2})
    s.call("POST", f"/reservations/{sr['occurrences'][2]['reference']}/cancel", token=ada)
    mk = s.new_key()
    mv_items = [{"reference": sr["occurrences"][1]["reference"], "table_id": "t_3"}]
    mv = moves(ada, mv_items, key=mk).json()
    refs = [o["reference"] for o in sr["occurrences"]]
    before = {
        "detail": s.call("GET", "/restaurants/r_anker").json(),
        "policies": s.call("GET", "/restaurants/r_anker/policies").json(),
        "series": s.call("GET", f"/series/{sr['series_id']}", token=ada).json(),
        "res": [get(ada, r) for r in refs], "hist": [history(ada, r) for r in refs],
    }
    snap = s.expect("rt export", s.call("GET", "/_test/export"), 200).json()
    s.check("rt envelope", snap["track"] == "tablekeeper" and snap["format_version"] == 1)
    fresh(restaurants=[s.restaurant("r_other")])
    s.expect("rt import", s.call("POST", "/_test/import", body=snap), 204)
    after = {
        "detail": s.call("GET", "/restaurants/r_anker").json(),
        "policies": s.call("GET", "/restaurants/r_anker/policies").json(),
        "series": s.call("GET", f"/series/{sr['series_id']}", token=ada).json(),
        "res": [get(ada, r) for r in refs], "hist": [history(ada, r) for r in refs],
    }
    for k in before:
        s.check(f"rt {k} preserved", before[k] == after[k], f"{json.dumps(before[k])[:160]} != {json.dumps(after[k])[:160]}")
    s.check("rt policy replay", publish(ada, pol_body, key=pk).json() == p1)
    s.check("rt booking replay", book(ada, key=bk).json() == b)
    s.check("rt series replay", series(ada, ser_body, key=sk).json() == sr)
    s.check("rt moves replay", moves(ada, mv_items, key=mk).json() == mv)
    s.check("rt next policy version", publish(ada, policy(d(70))).json()["policy_version"] == 2)
    patch(ada, refs[0], {"party_size": 2})
    s.check("rt series revision continues", s.call("GET", f"/series/{sr['series_id']}", token=ada).json()["revision"] == before["series"]["revision"] + 1)
    s.expect("rt import twice", s.call("POST", "/_test/import", body=snap), 204)
    s.check("rt import is replacement", s.call("GET", f"/series/{sr['series_id']}", token=ada).json() == before["series"])


# ---- concurrency ------------------------------------------------------------------

def test_concurrency():
    ada, _ = fresh()
    a = s.expect("cc3 anchor", book(ada), 201).json()
    key = s.new_key()
    body = {"anchor_reference": a["reference"], "count": 4, "interval_weeks": 1}
    out = s.burst(lambda i: series(ada, body, key=key), 50)
    counts = s.tally(out)
    s.check("cc3 series once", counts.get(201) == 1 and counts.get(200) == 49 and len({r.body for r in out}) == 1, str(counts))
    s.check("cc3 one series of reservations", len(s.call("GET", "/reservations", token=ada).json()["reservations"]) == 4)
    out = s.burst(lambda i: publish(ada, policy(d(40 + i))), 50)
    versions = sorted(r.json()["policy_version"] for r in out if r.status == 201)
    s.check("cc3 policy versions unique", versions == list(range(1, 51)), str(s.tally(out)))
    out = s.burst(lambda i: publish(ada, policy(s.DATE, reservation_duration_minutes=60)) if i % 10 == 0 else book(ada, table=f"t_{1 + i % 3}", party=1, at=["18:00", "19:30", "21:00"][i % 3], date=d(i % 5)), 50)
    s.check("cc3 mixed no 5xx", all(0 < r.status < 500 for r in out), str(s.tally(out)))
    occupied = {}
    clash = False
    for r in s.call("GET", "/reservations", token=ada).json()["reservations"]:
        if r["status"] != "confirmed":
            continue
        for t in r["table_ids"]:
            for other in occupied.get(t, []):
                clash |= r["starts_at"] < other["ends_at"] and other["starts_at"] < r["ends_at"]
            occupied.setdefault(t, []).append(r)
    s.check("cc3 no overlapping bookings", not clash)


def run(module, stage1_url=None, stage2_url=None):
    global s
    s = module
    test_policies()
    test_own_duration_overlap()
    test_revisions_and_history()
    test_accepted_cutoff()
    test_concurrent_amendments()
    test_explain()
    test_series()
    test_moves()
    test_restaurant_revision()
    test_roundtrip()
    test_concurrency()
    for label, url, pairs in (("stage-1", stage1_url, False), ("stage-2", stage2_url, True)):
        if url:
            test_upgrade(label, url.rstrip("/"), pairs)
        else:
            print(f"note: no {label} URL given; {label} upgrade check skipped")
