"""Stage-4 HTTP checks for Tablekeeper, run from tests/smoke.py after stage 3.

Covers closure replans (validation, exact optimisation with each tie-break
deciding a hand-built case, planning bounds, no_feasible_plan, preview changing
nothing), plan application (history, revisions, series, stale_plan,
plan_already_applied, replays, concurrency), closures in availability, explain,
creates, amendments, moves, adoptions and series amendments, series
amendments themselves, export/import round trips and upgrades from real
stage-1, stage-2 and stage-3 exports. Shares smoke.py and stage3_smoke helpers.
"""
from __future__ import annotations

import datetime as dt
import json
from zoneinfo import ZoneInfo

import stage3_smoke as t3

s = None
BERLIN = ZoneInfo("Europe/Berlin")


def inst(date: str, hhmm: str) -> str:
    """An RFC 3339 instant for a Berlin wall-clock time, with its real offset."""
    h, m = map(int, hhmm.split(":"))
    return dt.datetime.combine(dt.date.fromisoformat(date), dt.time(h, m), tzinfo=BERLIN).isoformat()


def plan_restaurant(tables, pairs=(), **over):
    """A managed restaurant with the given (id, capacity) tables and pairs."""
    return s.restaurant(manager_user_ids=["u_ada"], combinable=[list(p) for p in pairs],
                        tables=[{"id": t, "label": t.upper(), "capacity": c} for t, c in tables], **over)


def seed(ref, table_ids, at, party, date=None, user="u_bob", rid="r_anker"):
    return {"id": "res_" + ref.lower(), "reference": ref, "user_id": user, "restaurant_id": rid,
            "table_ids": table_ids, "starts_at_local": f"{date or s.DATE}T{at}", "party_size": party}


def replan(token, table, frm, to, key=None, rid="r_anker"):
    return s.call("POST", f"/restaurants/{rid}/replans", body={"table_id": table, "from": frm, "to": to}, token=token, key=key or s.new_key())


def apply(token, plan_id, key=None, rid="r_anker", body=None):
    return s.call("POST", f"/restaurants/{rid}/replans/{plan_id}/apply", body={} if body is None else body, token=token, key=key or s.new_key())


def amend(token, series_id, body, key=None):
    return s.call("POST", f"/series/{series_id}/amend", body=body, token=token, key=key or s.new_key())


def evening(date=None):
    return inst(date or s.DATE, "18:00"), inst(date or s.DATE, "23:00")


def assignments(plan):
    return [(a["reference"], a["table_ids"], a["changed"]) for a in plan["assignments"]]


# ---- replan validation and preview ------------------------------------------------

def test_replan_validation():
    ada, bob = t3.fresh(restaurants=[plan_restaurant([("t_1", 2), ("t_2", 4), ("t_3", 6)], [("t_1", "t_2"), ("t_2", "t_3")])])
    frm, to = evening()
    check, expect = s.check, s.expect
    expect("rp no token", s.call("POST", "/restaurants/r_anker/replans", body={"table_id": "t_2", "from": frm, "to": to}, key=s.new_key()), 401, "unauthenticated")
    expect("rp non-manager", replan(bob, "t_2", frm, to), 403, "forbidden")
    expect("rp unknown restaurant", replan(ada, "t_2", frm, to, rid="r_nope"), 404, "not_found")
    expect("rp missing key", s.call("POST", "/restaurants/r_anker/replans", body={"table_id": "t_2", "from": frm, "to": to}, token=ada), 400, "missing_idempotency_key")
    expect("rp malformed", s.call("POST", "/restaurants/r_anker/replans", raw=b"[", token=ada, key=s.new_key()), 400, "malformed_request")
    expect("rp from after to", replan(ada, "t_2", to, frm), 422, "validation_failed")
    expect("rp empty interval", replan(ada, "t_2", frm, frm), 422, "validation_failed")
    expect("rp no offset", replan(ada, "t_2", f"{s.DATE}T18:00:00", to), 422, "validation_failed")
    expect("rp not a time", replan(ada, "t_2", "tonight", to), 422, "validation_failed")
    expect("rp missing to", s.call("POST", "/restaurants/r_anker/replans", body={"table_id": "t_2", "from": frm}, token=ada, key=s.new_key()), 422, "validation_failed")
    expect("rp table wrong type", s.call("POST", "/restaurants/r_anker/replans", body={"table_id": 2, "from": frm, "to": to}, token=ada, key=s.new_key()), 400, "malformed_request")
    expect("rp unknown table", replan(ada, "t_9", frm, to), 404, "not_found")
    utc_from = dt.datetime.fromisoformat(frm).astimezone(dt.timezone.utc).isoformat()
    p = expect("rp offsets compared as instants", replan(ada, "t_2", utc_from, to), 201).json()
    check("rp empty plan", p["assignments"] == [] and p["moved_count"] == 0 and p["unused_seats"] == 0
          and p["closure"] == {"table_id": "t_2", "from": utc_from, "to": to} and p["restaurant_revision"] == 0 and p["plan_id"])


def test_preview_changes_nothing():
    ada, bob = t3.fresh(restaurants=[plan_restaurant([("t_1", 2), ("t_2", 4), ("t_3", 6)], [("t_1", "t_2")])])
    b = s.expect("pv booking", t3.book(bob, table="t_2", party=2), 201).json()
    rev = t3.detail_rev()
    avail = s.availability("r_anker", s.DATE, 2).json()
    frm, to = evening()
    key = s.new_key()
    p = s.expect("pv preview", replan(ada, "t_2", frm, to, key=key), 201).json()
    s.check("pv plan moves booking", assignments(p) == [(b["reference"], ["t_1"], True)] and p["moved_count"] == 1 and p["unused_seats"] == 0, json.dumps(p))
    s.check("pv revision recorded", p["restaurant_revision"] == rev)
    s.check("pv nothing changed", t3.detail_rev() == rev and t3.get(bob, b["reference"]) == b and len(t3.history(bob, b["reference"])) == 1
            and s.availability("r_anker", s.DATE, 2).json() == avail)
    s.check("pv replay", s.expect("pv replay status", replan(ada, "t_2", frm, to, key=key), 200).json() == p)
    s.expect("pv key reuse", replan(ada, "t_3", frm, to, key=key), 409, "idempotency_key_reuse")
    s.expect("pv booking still allowed on closing table", t3.book(ada, table="t_2", party=2, at="21:30"), 201)


# ---- the optimisation: each tie-break decides one hand-built case --------------------

def test_optimisation_cases():
    ada, _ = t3.fresh(restaurants=[plan_restaurant([("t_1", 2), ("t_2", 4), ("t_3", 6), ("t_4", 4)])],
                      reservations=[seed("AAAA11", ["t_2"], "19:00", 2), seed("BBBB22", ["t_3"], "19:00", 2)])
    frm, to = evening()
    p = s.expect("opt moves", replan(ada, "t_2", frm, to), 201).json()
    s.check("opt 1: fewest moved wins over fewer unused seats",
            assignments(p) == [("AAAA11", ["t_1"], True), ("BBBB22", ["t_3"], False)] and p["moved_count"] == 1 and p["unused_seats"] == 4, json.dumps(p))

    ada, _ = t3.fresh(restaurants=[plan_restaurant([("t_1", 2), ("t_2", 4), ("t_3", 6), ("t_4", 4)])],
                      reservations=[seed("AAAA11", ["t_2"], "19:00", 3)])
    p = s.expect("opt unused", replan(ada, "t_2", frm, to), 201).json()
    s.check("opt 2: fewer unused seats wins over lower rank", assignments(p) == [("AAAA11", ["t_4"], True)] and p["unused_seats"] == 1, json.dumps(p))

    ada, _ = t3.fresh(restaurants=[plan_restaurant([("t_1", 2), ("t_2", 4), ("t_3", 4), ("t_4", 4)])],
                      reservations=[seed("AAAA11", ["t_2"], "19:00", 3)])
    p = s.expect("opt rank", replan(ada, "t_2", frm, to), 201).json()
    s.check("opt 3: equal unused, lower rank wins", assignments(p) == [("AAAA11", ["t_3"], True)], json.dumps(p))

    ada, _ = t3.fresh(restaurants=[plan_restaurant([("t_1", 3), ("t_2", 6), ("t_3", 6), ("t_4", 3)], [("t_1", "t_4")])],
                      reservations=[seed("AAAA11", ["t_2"], "19:00", 6)])
    p = s.expect("opt single before pair", replan(ada, "t_2", frm, to), 201).json()
    s.check("opt 3b: singles rank before pairs", assignments(p) == [("AAAA11", ["t_3"], True)], json.dumps(p))

    # a fixed booking outside the closure still blocks an option during the booking's own interval
    ada, _ = t3.fresh(restaurants=[plan_restaurant([("t_1", 3), ("t_2", 6), ("t_3", 6), ("t_4", 3)], [("t_1", "t_4")])],
                      reservations=[seed("AAAA11", ["t_2"], "19:00", 6), seed("FFFF99", ["t_3"], "20:00", 2)])
    p = s.expect("opt fixed blocks", replan(ada, "t_2", inst(s.DATE, "19:00"), inst(s.DATE, "19:30")), 201).json()
    s.check("opt fixed booking excluded from plan and blocks t_3", assignments(p) == [("AAAA11", ["t_1", "t_4"], True)], json.dumps(p))

    # rank vector in reference order: two movers compete for the lowest-rank table
    ada, _ = t3.fresh(restaurants=[plan_restaurant([("t_1", 4), ("t_2", 4), ("t_3", 4), ("t_4", 4)], [("t_1", "t_2")])],
                      reservations=[seed("BBBB22", ["t_1", "t_2"], "19:00", 4), seed("AAAA11", ["t_4"], "19:00", 4)])
    p = s.expect("opt vector", replan(ada, "t_1", frm, to), 201).json()
    s.check("opt pair holder moves, other stays", assignments(p) == [("AAAA11", ["t_4"], False), ("BBBB22", ["t_2"], True)], json.dumps(p))
    s.expect("opt vector reset", s.reset(s.fixture(restaurants=[plan_restaurant([("t_1", 4), ("t_2", 4), ("t_3", 4), ("t_4", 4)])],
             reservations=[seed("CCCC33", ["t_1"], "20:00", 4), seed("AAAA11", ["t_2"], "19:00", 4)])), 204)
    ada = s.login(s.ADA["email"], s.ADA["password"])
    p = s.expect("opt two closed", replan(ada, "t_1", frm, to), 201).json()
    s.check("opt only the closed table's booking moves", assignments(p) == [("AAAA11", ["t_2"], False), ("CCCC33", ["t_3"], True)], json.dumps(p))

    # own accepted terms: a later policy with a bigger t_1 does not help a booking made under policy 0
    ada, bob = t3.fresh(restaurants=[plan_restaurant([("t_1", 2), ("t_2", 4), ("t_3", 6)])])
    b = s.expect("opt own terms booking", t3.book(bob, table="t_2", party=4), 201).json()
    s.expect("opt bigger policy", t3.publish(ada, {**t3.policy(s.DATE), "capacities": {"t_1": 6, "t_2": 4, "t_3": 6}}), 201)
    p = s.expect("opt own terms plan", replan(ada, "t_2", frm, to), 201).json()
    s.check("opt capacity from the booking's own terms", assignments(p) == [(b["reference"], ["t_3"], True)] and p["unused_seats"] == 2, json.dumps(p))

    ada, _ = t3.fresh(restaurants=[plan_restaurant([("t_1", 2), ("t_2", 6), ("t_3", 4)])],
                      reservations=[seed("AAAA11", ["t_2"], "19:00", 6)])
    key = s.new_key()
    rev = t3.detail_rev()
    s.expect("opt no feasible plan", replan(ada, "t_2", frm, to, key=key), 409, "no_feasible_plan")
    s.check("opt infeasible changes nothing", t3.detail_rev() == rev)
    s.expect("opt failed key reusable", replan(ada, "t_3", frm, to, key=key), 201)


def test_planning_limits():
    frm, to = evening()
    six = [(f"t_{i}", 4) for i in range(1, 7)]
    four_pairs = [("t_1", "t_2"), ("t_2", "t_3"), ("t_3", "t_4"), ("t_4", "t_5")]
    seeds6 = [seed(f"REF{i:03d}", [f"t_{i}"], "19:00", 1) for i in range(1, 6)] + [seed("REF006", ["t_6"], "21:00", 1)]
    ada, _ = t3.fresh(restaurants=[plan_restaurant(six, four_pairs)], reservations=seeds6)
    p = s.expect("lim at bound (6 tables, 4 pairs, 6 bookings)", replan(ada, "t_6", frm, to), 201).json()
    s.check("lim bound plan exact", len(p["assignments"]) == 6 and p["moved_count"] == 1, json.dumps(p)[:300])
    ada, _ = t3.fresh(restaurants=[plan_restaurant(six + [("t_7", 4)])])
    s.expect("lim 7 tables", replan(ada, "t_1", frm, to), 422, "planning_limit")
    ada, _ = t3.fresh(restaurants=[plan_restaurant(six, four_pairs + [("t_5", "t_6")])])
    s.expect("lim 5 pairs", replan(ada, "t_1", frm, to), 422, "planning_limit")
    seeds7 = seeds6 + [seed("REF007", ["t_1"], "21:00", 1)]
    ada, _ = t3.fresh(restaurants=[plan_restaurant(six, four_pairs)], reservations=seeds7)
    s.expect("lim 7 bookings", replan(ada, "t_6", frm, to), 422, "planning_limit")


# ---- application ----------------------------------------------------------------------

def test_apply():
    ada, bob = t3.fresh(restaurants=[plan_restaurant([("t_1", 2), ("t_2", 4), ("t_3", 6), ("t_4", 4)], [("t_1", "t_2"), ("t_3", "t_4")])])
    check, expect = s.check, s.expect
    moved = expect("ap booking to move", t3.book(bob, table="t_2", party=4), 201).json()
    stay = expect("ap booking that stays", t3.book(bob, table="t_3", party=2, at="20:00"), 201).json()
    frm, to = evening()
    p = expect("ap preview", replan(ada, "t_2", frm, to), 201).json()
    refs = sorted([moved["reference"], stay["reference"]])
    check("ap plan", [a["reference"] for a in p["assignments"]] == refs and p["moved_count"] == 1, json.dumps(p))
    target = next(a for a in p["assignments"] if a["reference"] == moved["reference"])["table_ids"]
    expect("ap no token", s.call("POST", f"/restaurants/r_anker/replans/{p['plan_id']}/apply", body={}, key=s.new_key()), 401, "unauthenticated")
    expect("ap non-manager", apply(bob, p["plan_id"]), 403, "forbidden")
    expect("ap unknown plan", apply(ada, "plan_nope"), 404, "not_found")
    expect("ap missing key", s.call("POST", f"/restaurants/r_anker/replans/{p['plan_id']}/apply", body={}, token=ada), 400, "missing_idempotency_key")
    key = s.new_key()
    rev = t3.detail_rev()
    r = expect("ap apply", apply(ada, p["plan_id"], key=key, body={"note": "ignored"}), 201).json()
    check("ap response", r["plan_id"] == p["plan_id"] and r["restaurant_revision"] == rev + 1 == t3.detail_rev()
          and [x["reference"] for x in r["reservations"]] == refs)
    m = t3.get(bob, moved["reference"])
    check("ap moved booking", m["table_ids"] == target and m["revision"] == 2 and m["starts_at"] == moved["starts_at"]
          and m["ends_at"] == moved["ends_at"] and m["accepted_terms"] == moved["accepted_terms"] and m["party_size"] == 4
          and ("table_id" in m) == (len(target) == 1), json.dumps(m))
    h = t3.history(bob, moved["reference"])
    check("ap reassigned entry", len(h) == 2 and h[1]["event"] == "reassigned" and h[1]["plan_id"] == p["plan_id"]
          and h[1]["changes"] == [{"field": "table_ids", "from": ["t_2"], "to": target}] and h[1]["revision"] == 2
          and h[1]["accepted_terms"] == moved["accepted_terms"], json.dumps(h[1]))
    check("ap unmoved gains nothing", t3.get(bob, stay["reference"]) == stay and len(t3.history(bob, stay["reference"])) == 1)
    slot = next(x for x in t3.explain(s.DATE, 2)["slots"] if x["starts_at_local"].endswith("18:00"))
    t2 = next(e for e in slot["explain"] if e["table_id"] == "t_2")
    check("ap closure excludes table", "t_2" not in slot["available_table_ids"] and t2["rules"][1] == {"rule": "no_overlap", "holds": False})
    check("ap closure excludes pairs", all("t_2" not in o["table_ids"] for o in slot["available_options"]))
    check("ap other day unaffected", "t_2" in s.slot_tables("r_anker", t3.d(1), 2, "19:00"))
    expect("ap create on closed table", t3.book(bob, table="t_2", party=2, at="21:30"), 409, "table_unavailable")
    expect("ap patch onto closed table", t3.patch(bob, stay["reference"], {"table_id": "t_2"}), 409, "table_unavailable")
    expect("ap move onto closed table", t3.moves(bob, [{"reference": stay["reference"], "table_ids": ["t_1", "t_2"]}]), 409, "table_unavailable")
    before = t3.detail_rev()
    expect("ap other key", apply(ada, p["plan_id"]), 409, "plan_already_applied")
    expect("ap other manager key same string", apply(ada, p["plan_id"], key=key, body={"x": 1}), 409, "idempotency_key_reuse")
    expect("ap later change", t3.patch(bob, stay["reference"], {"party_size": 3}), 200)
    check("ap replay after later change", expect("ap replay status", apply(ada, p["plan_id"], key=key, body={"note": "ignored"}), 200).json() == r)
    check("ap failures and replays change nothing", t3.detail_rev() == before + 1)

    # stale plans, and closures elsewhere do not stale a plan
    s.expect("ap reset two", s.reset(s.fixture(restaurants=[
        plan_restaurant([("t_1", 2), ("t_2", 4), ("t_3", 6)]),
        {**plan_restaurant([("t_1", 2), ("t_2", 4), ("t_3", 6)]), "id": "r_two", "name": "Two"}])), 204)
    ada = s.login(s.ADA["email"], s.ADA["password"])
    p1 = expect("ap preview r_anker", replan(ada, "t_2", frm, to), 201).json()
    p2 = expect("ap preview r_two", replan(ada, "t_2", frm, to, rid="r_two"), 201).json()
    expect("ap plan of another restaurant", apply(ada, p2["plan_id"]), 404, "not_found")
    expect("ap apply r_two", apply(ada, p2["plan_id"], rid="r_two"), 201)
    expect("ap other restaurant does not stale", apply(ada, p1["plan_id"]), 201)
    p3 = expect("ap preview to stale", replan(ada, "t_3", frm, to), 201).json()
    expect("ap intervening booking", t3.book(ada, table="t_1", party=2, date=t3.d(3)), 201)
    rev = t3.detail_rev()
    expect("ap stale plan", apply(ada, p3["plan_id"]), 409, "stale_plan")
    check("ap stale changes nothing", t3.detail_rev() == rev and "t_3" in s.slot_tables("r_anker", s.DATE, 2, "19:00"))


def test_apply_series_and_concurrency():
    ada, bob = t3.fresh(restaurants=[plan_restaurant([("t_1", 4), ("t_2", 4), ("t_3", 6)])])
    a = s.expect("aps anchor", t3.book(bob, table="t_2", party=4, date=t3.d(-1)), 201).json()
    sr = s.expect("aps series", t3.series(bob, {"anchor_reference": a["reference"], "count": 3, "interval_weeks": 1}), 201).json()
    occ = sr["occurrences"]
    s.expect("aps exception", t3.patch(bob, occ[2]["reference"], {"party_size": 3}), 200)
    frm, to = evening(t3.d(6))
    p = s.expect("aps preview", replan(ada, "t_2", frm, to), 201).json()
    s.check("aps occurrence considered", [x["reference"] for x in p["assignments"]] == [occ[1]["reference"]] and p["moved_count"] == 1)
    rev_before = s.call("GET", f"/series/{sr['series_id']}", token=bob).json()["revision"]
    s.expect("aps apply", apply(ada, p["plan_id"]), 201)
    g = s.call("GET", f"/series/{sr['series_id']}", token=bob).json()
    s.check("aps series revision once, flags and dates kept", g["revision"] == rev_before + 1
            and [o["exception"] for o in g["occurrences"]] == [False, False, True]
            and g["occurrences"][1]["reservation"]["starts_at_local"] == occ[1]["reservation"]["starts_at_local"]
            and g["occurrences"][1]["reservation"]["table_ids"] == ["t_1"], json.dumps(g)[:300])
    # concurrency: one plan, many applications
    ada, bob = t3.fresh(restaurants=[plan_restaurant([("t_1", 4), ("t_2", 4), ("t_3", 6)])])
    for at in ("18:00", "20:00"):
        s.expect(f"apc booking {at}", t3.book(bob, table="t_2", party=2, at=at), 201)
    frm, to = evening()
    p = s.expect("apc preview", replan(ada, "t_2", frm, to), 201).json()
    out = s.burst(lambda i: apply(ada, p["plan_id"]), 20)
    counts = s.tally(out)
    s.check("apc one application", counts.get(201) == 1 and counts.get(409) == 19, str(counts))
    s.check("apc no partial moves", all(t3.get(bob, a["reference"])["revision"] == 2 for a in p["assignments"]))
    p = s.expect("apc preview 2", replan(ada, "t_1", *evening(t3.d(1))), 201).json()
    key = s.new_key()
    out = s.burst(lambda i: apply(ada, p["plan_id"], key=key), 20)
    counts = s.tally(out)
    s.check("apc same key once", counts.get(201) == 1 and counts.get(200) == 19 and len({r.body for r in out}) == 1, str(counts))


# ---- series amendments --------------------------------------------------------------

def test_series_amend():
    ada, bob = t3.fresh(restaurants=[plan_restaurant([("t_1", 2), ("t_2", 4), ("t_3", 6)])])
    check, expect = s.check, s.expect
    anchor = expect("am anchor", t3.book(ada, table="t_2", party=4), 201).json()
    sr = expect("am series", t3.series(ada, {"anchor_reference": anchor["reference"], "count": 4, "interval_weeks": 1}), 201).json()
    sid, occ = sr["series_id"], sr["occurrences"]
    good = {"expected_revision": 1, "from_index": 0, "local_time": "20:00"}
    for name, over in (("rev 0", {"expected_revision": 0}), ("rev bool", {"expected_revision": True}), ("rev string", {"expected_revision": "1"}),
                       ("rev float", {"expected_revision": 1.5}), ("index -1", {"from_index": -1}), ("index count", {"from_index": 4}),
                       ("index bool", {"from_index": False}), ("index string", {"from_index": "0"}), ("time 24:00", {"local_time": "24:00"}),
                       ("time 7:00", {"local_time": "7:00"}), ("time seconds", {"local_time": "20:00:00"}), ("time 20:60", {"local_time": "20:60"}),
                       ("time number", {"local_time": 2000})):
        expect(f"am invalid {name}", amend(ada, sid, {**good, **over}), 422, "validation_failed")
    for field in good:
        expect(f"am missing {field}", amend(ada, sid, {k: v for k, v in good.items() if k != field}), 422, "validation_failed")
    expect("am no token", s.call("POST", f"/series/{sid}/amend", body=good, key=s.new_key()), 401, "unauthenticated")
    expect("am other owner", amend(bob, sid, good), 404, "not_found")
    expect("am unknown", amend(ada, "ser_nope", good), 404, "not_found")
    expect("am missing key", s.call("POST", f"/series/{sid}/amend", body=good, token=ada), 400, "missing_idempotency_key")
    expect("am stale", amend(ada, sid, {**good, "expected_revision": 2}), 409, "stale_revision")
    # exception and cancelled occurrences are not eligible
    expect("am make exception", t3.patch(ada, occ[2]["reference"], {"party_size": 3}), 200)
    expect("am cancel one", s.call("POST", f"/reservations/{occ[3]['reference']}/cancel", token=ada), 200)
    rev = t3.detail_rev()
    key = s.new_key()
    body = {"expected_revision": 3, "from_index": 1, "local_time": "20:30", "note": "ignored"}
    r = expect("am amend", amend(ada, sid, body, key=key), 201).json()
    times = [o["reservation"]["starts_at_local"][11:] for o in r["occurrences"]]
    check("am only eligible changed", times == ["19:00", "20:30", "19:00", "19:00"] and r["revision"] == 4, json.dumps(times))
    check("am no exception marked", [o["exception"] for o in r["occurrences"]] == [False, False, True, False])
    check("am dates kept", r["occurrences"][1]["reservation"]["starts_at_local"] == f"{t3.d(7)}T20:30")
    h = t3.history(ada, occ[1]["reference"])
    check("am ordinary changed entry", [e["event"] for e in h] == ["created", "changed"] and h[1]["changes"] == [
        {"field": "starts_at_local", "from": f"{t3.d(7)}T19:00", "to": f"{t3.d(7)}T20:30"}] and h[1]["revision"] == 2)
    check("am restaurant revision once", t3.detail_rev() == rev + 1)
    check("am get equals response", s.call("GET", f"/series/{sid}", token=ada).json() == r)
    nr = expect("am no-op", amend(ada, sid, {"expected_revision": 4, "from_index": 1, "local_time": "20:30"}), 201).json()
    check("am no-op changes nothing", nr == r and t3.detail_rev() == rev + 1)
    er = expect("am empty eligible", amend(ada, sid, {"expected_revision": 4, "from_index": 3, "local_time": "21:00"}), 201).json()
    check("am empty set changes nothing", er["revision"] == 4)
    expect("am later edit", t3.patch(ada, occ[1]["reference"], {"party_size": 2}), 200)
    check("am replay after later edit", expect("am replay status", amend(ada, sid, body, key=key), 200).json() == r)
    expect("am key reuse", amend(ada, sid, {**body, "local_time": "21:00"}, key=key), 409, "idempotency_key_reuse")

    # policies, precedence, occupancy, closures, cutoff, DST, concurrency
    ada, bob = t3.fresh(restaurants=[plan_restaurant([("t_1", 2), ("t_2", 4), ("t_3", 6)])])
    anchor = expect("am2 anchor", t3.book(ada, table="t_2", party=4), 201).json()
    sr = expect("am2 series", t3.series(ada, {"anchor_reference": anchor["reference"], "count": 3, "interval_weeks": 1}), 201).json()
    sid, occ = sr["series_id"], sr["occurrences"]
    expect("am2 policy week 1", t3.publish(ada, t3.policy(t3.d(7), slot_minutes=60, reservation_duration_minutes=60)), 201)
    expect("am2 bob at week 2 21:00", t3.book(bob, table="t_2", party=2, at="21:00", date=t3.d(14)), 201)
    rev = t3.detail_rev()
    expect("am2 grid error precedes occupancy", amend(ada, sid, {"expected_revision": 1, "from_index": 0, "local_time": "20:30"}), 422, "not_on_slot_grid")
    expect("am2 occupancy", amend(ada, sid, {"expected_revision": 1, "from_index": 2, "local_time": "21:00"}), 409, "table_unavailable")
    check("am2 failures change nothing", t3.detail_rev() == rev and s.call("GET", f"/series/{sid}", token=ada).json() == sr
          and all(len(t3.history(ada, o["reference"])) == 1 for o in occ))
    r = expect("am2 adopts each date's policy", amend(ada, sid, {"expected_revision": 1, "from_index": 1, "local_time": "22:00"}), 201).json()
    o1 = r["occurrences"][1]["reservation"]
    check("am2 week 1 policy", o1["accepted_terms"]["policy_version"] == 1 and o1["ends_at"].startswith(f"{t3.d(7)}T23:00:00"))
    s.expect("am2 close", apply(ada, s.expect("am2 plan", replan(ada, "t_2", inst(t3.d(14), "17:00"), inst(t3.d(14), "21:00")), 201).json()["plan_id"]), 201)
    g = s.call("GET", f"/series/{sid}", token=ada).json()
    expect("am2 closure blocks amendment", amend(ada, sid, {"expected_revision": g["revision"], "from_index": 2, "local_time": "19:00"}), 409, "table_unavailable")
    ada, _ = t3.fresh(restaurants=[plan_restaurant([("t_1", 2), ("t_2", 4), ("t_3", 6)])])
    a = expect("am3 anchor", t3.book(ada, table="t_2", party=4), 201).json()
    sr = expect("am3 series", t3.series(ada, {"anchor_reference": a["reference"], "count": 2, "interval_weeks": 1}), 201).json()
    out = s.burst(lambda i: amend(ada, sr["series_id"], {"expected_revision": 1, "from_index": 0, "local_time": ["20:00", "20:30"][i % 2]}), 20)
    counts = s.tally(out)
    s.check("am3 concurrent amendments: one real change", counts.get(201) == 1 and counts.get(409) == 19
            and {x.code() for x in out if x.status == 409} == {"stale_revision"}, str(counts))
    s.check("am3 one history entry each", all(len(t3.history(ada, o["reference"])) == 2 for o in sr["occurrences"]))
    ada, _ = t3.fresh(restaurants=[plan_restaurant([("t_1", 2), ("t_2", 4), ("t_3", 6)], opening_hours=s.all_week("00:00", "23:30"))])
    a = expect("am4 anchor", t3.book(ada, table="t_2", party=4, at="03:30", date="2027-03-21"), 201).json()
    sr = expect("am4 series", t3.series(ada, {"anchor_reference": a["reference"], "count": 2, "interval_weeks": 1}), 201).json()
    expect("am4 nonexistent local time", amend(ada, sr["series_id"], {"expected_revision": 1, "from_index": 0, "local_time": "02:30"}), 422, "invalid_local_time")
    check("am4 nothing changed", s.call("GET", f"/series/{sr['series_id']}", token=ada).json() == sr)


def test_amend_cutoff():
    """Stale is decided before any occurrence's cutoff; then the old accepted cutoff applies."""
    ada, _ = t3.fresh(restaurants=[plan_restaurant([("t_1", 2), ("t_2", 4), ("t_3", 6)])])
    a = s.expect("amc anchor", t3.book(ada, table="t_2", party=4), 201).json()
    sr = s.expect("amc series", t3.series(ada, {"anchor_reference": a["reference"], "count": 2, "interval_weeks": 1}), 201).json()
    snap = s.call("GET", "/_test/export").json()
    for r in snap["state"]["restaurants"]:
        r["cancellation_cutoff_minutes"] = t3.HUGE_CUTOFF
    for r in snap["state"]["reservations"]:
        r["accepted_terms"]["cancellation_cutoff_minutes"] = t3.HUGE_CUTOFF
    s.expect("amc import huge cutoff", s.call("POST", "/_test/import", body=snap), 204)
    s.expect("amc stale first", amend(ada, sr["series_id"], {"expected_revision": 9, "from_index": 0, "local_time": "20:00"}), 409, "stale_revision")
    s.expect("amc cutoff", amend(ada, sr["series_id"], {"expected_revision": 1, "from_index": 0, "local_time": "20:00"}), 409, "cutoff_passed")


def test_closures_on_adoption():
    ada, bob = t3.fresh(restaurants=[plan_restaurant([("t_1", 2), ("t_2", 4), ("t_3", 6)], [("t_1", "t_2"), ("t_2", "t_3")])])
    s.expect("cl apply", apply(ada, s.expect("cl plan", replan(ada, "t_2", *evening(t3.d(7))), 201).json()["plan_id"]), 201)
    a = s.expect("cl anchor", t3.book(bob, table="t_2", party=4), 201).json()
    s.expect("cl adoption hits closure", t3.series(bob, {"anchor_reference": a["reference"], "count": 2, "interval_weeks": 1}), 409, "table_unavailable")
    opts = s.availability("r_anker", t3.d(7), 2).json()["slots"][0]["available_options"]
    s.check("cl pairs with the closed table excluded", [o["table_ids"] for o in opts] == [["t_1"], ["t_3"]], json.dumps(opts))
    opts = s.availability("r_anker", t3.d(8), 2).json()["slots"][0]["available_options"]
    s.check("cl pairs back after the closure", [o["table_ids"] for o in opts] == [["t_1"], ["t_2"], ["t_3"], ["t_1", "t_2"], ["t_2", "t_3"]], json.dumps(opts))


# ---- export / import ----------------------------------------------------------------

def test_roundtrip():
    ada, bob = t3.fresh(restaurants=[plan_restaurant([("t_1", 2), ("t_2", 4), ("t_3", 6)], [("t_1", "t_2")])])
    b = s.expect("rt4 booking", t3.book(bob, table="t_2", party=2), 201).json()
    pk, ak = s.new_key(), s.new_key()
    p = s.expect("rt4 plan", replan(ada, "t_2", *evening(), key=pk), 201).json()
    r = s.expect("rt4 apply", apply(ada, p["plan_id"], key=ak), 201).json()
    pending = s.expect("rt4 pending", replan(ada, "t_3", *evening(t3.d(1))), 201).json()
    snap = s.call("GET", "/_test/export").json()
    before = (s.call("GET", "/restaurants/r_anker").json(), s.availability("r_anker", s.DATE, 2).json(), t3.get(bob, b["reference"]), t3.history(bob, b["reference"]))
    t3.fresh(restaurants=[s.restaurant("r_other")])
    s.expect("rt4 import", s.call("POST", "/_test/import", body=snap), 204)
    after = (s.call("GET", "/restaurants/r_anker").json(), s.availability("r_anker", s.DATE, 2).json(), t3.get(bob, b["reference"]), t3.history(bob, b["reference"]))
    s.check("rt4 state preserved", before == after, json.dumps(after[0])[:200])
    s.check("rt4 receipts replay", replan(ada, "t_2", *evening(), key=pk).json() == p and apply(ada, p["plan_id"], key=ak).json() == r)
    s.expect("rt4 applied stays applied", apply(ada, p["plan_id"]), 409, "plan_already_applied")
    s.expect("rt4 pending still applicable", apply(ada, pending["plan_id"]), 201)
    s.expect("rt4 closure still blocks", t3.book(bob, table="t_2", party=2, at="21:30"), 409, "table_unavailable")
    s.expect("rt4 reset clears closures", s.reset(s.fixture(restaurants=[plan_restaurant([("t_1", 2), ("t_2", 4), ("t_3", 6)])])), 204)
    s.check("rt4 reset revision 0 and open", t3.detail_rev() == 0 and "t_2" in s.slot_tables("r_anker", s.DATE, 2, "19:00"))


def test_upgrade_stage3(base):
    src = t3.remote(base)
    rest = plan_restaurant([("t_1", 2), ("t_2", 4), ("t_3", 6)], [("t_1", "t_2")])
    assert src("POST", "/_test/reset", s.fixture(restaurants=[rest]))[0] == 204
    tok = src("POST", "/auth/login", {"email": s.ADA["email"], "password": s.ADA["password"]})[1]["token"]
    st, pol = src("POST", "/restaurants/r_anker/policies", t3.policy(t3.d(7), reservation_duration_minutes=60), tok, s.new_key())
    k1 = s.new_key()
    body1 = {"restaurant_id": "r_anker", "table_id": "t_2", "starts_at_local": f"{s.DATE}T19:00", "party_size": 2}
    _, b1 = src("POST", "/reservations", body1, tok, k1)
    ks = s.new_key()
    sbody = {"anchor_reference": b1["reference"], "count": 3, "interval_weeks": 1}
    _, sr = src("POST", "/series", sbody, tok, ks)
    occ = sr["occurrences"]
    src("PATCH", f"/reservations/{occ[1]['reference']}", {"party_size": 1}, tok)
    src("POST", f"/reservations/{occ[2]['reference']}/cancel", None, tok)
    _, rest_before = src("GET", "/restaurants/r_anker")
    _, hist_before = src("GET", f"/reservations/{occ[1]['reference']}/history", None, tok)
    snap = src("GET", "/_test/export")[1]
    s.reset(s.fixture(restaurants=[s.restaurant("r_other")]))
    s.expect("up3 import", s.call("POST", "/_test/import", body=snap), 204)
    s.check("up3 restaurant revision preserved", t3.detail_rev() == rest_before["revision"], f"{t3.detail_rev()} vs {rest_before['revision']}")
    s.check("up3 history preserved", t3.history(tok, occ[1]["reference"]) == hist_before["entries"])
    rep = s.expect("up3 booking receipt", s.call("POST", "/reservations", body=body1, token=tok, key=k1), 200).json()
    s.check("up3 booking receipt verbatim", rep == b1)
    s.check("up3 series receipt verbatim", s.expect("up3 series replay", t3.series(tok, sbody, key=ks), 200).json() == sr)
    g = s.call("GET", f"/series/{sr['series_id']}", token=tok).json()
    s.check("up3 series state", [o["exception"] for o in g["occurrences"]] == [False, True, False]
            and g["occurrences"][2]["reservation"]["status"] == "cancelled")
    a = s.expect("up3 amend imported series", amend(tok, sr["series_id"], {"expected_revision": g["revision"], "from_index": 0, "local_time": "20:00"}), 201).json()
    s.check("up3 amend skips exception and cancelled", [o["reservation"]["starts_at_local"][11:] for o in a["occurrences"]] == ["20:00", "19:00", "19:00"])
    p = s.expect("up3 replan imported", replan(tok, "t_2", *evening()), 201).json()
    s.check("up3 plan moves anchor", p["moved_count"] == 1 and p["assignments"][0]["reference"] == b1["reference"])
    s.expect("up3 apply", apply(tok, p["plan_id"]), 201)
    s.check("up3 moved occurrence keeps date", t3.get(tok, b1["reference"])["starts_at_local"] == f"{s.DATE}T20:00")


def test_upgrade_early(label, base, pairs):
    src = t3.remote(base)
    rest = s.restaurant(combinable=[["t_1", "t_2"]]) if pairs else s.restaurant()
    assert src("POST", "/_test/reset", s.fixture(restaurants=[rest]))[0] == 204
    tok = src("POST", "/auth/login", {"email": s.ADA["email"], "password": s.ADA["password"]})[1]["token"]
    k = s.new_key()
    body = {"restaurant_id": "r_anker", "table_id": "t_2", "starts_at_local": f"{s.DATE}T19:00", "party_size": 2}
    _, b = src("POST", "/reservations", body, tok, k)
    snap = src("GET", "/_test/export")[1]
    s.reset(s.fixture(restaurants=[s.restaurant("r_other")]))
    s.expect(f"up4 {label} import", s.call("POST", "/_test/import", body=snap), 204)
    s.check(f"up4 {label} revision 0", t3.detail_rev() == 0)
    s.check(f"up4 {label} receipt verbatim", s.call("POST", "/reservations", body=body, token=tok, key=k).json() == b)
    # managers are not part of earlier fixtures, so the plan is made on a re-seeded copy
    snap["state"]["restaurants"][0]["manager_user_ids"] = ["u_ada"]
    s.expect(f"up4 {label} import with manager", s.call("POST", "/_test/import", body=snap), 204)
    p = s.expect(f"up4 {label} replan", replan(tok, "t_2", *evening()), 201).json()
    s.check(f"up4 {label} plan", p["moved_count"] == 1 and p["restaurant_revision"] == 0)
    s.expect(f"up4 {label} apply", apply(tok, p["plan_id"]), 201)
    h = t3.history(tok, b["reference"])
    s.check(f"up4 {label} history", [e["event"] for e in h] == ["created", "reassigned"])


def run(module, stage1_url=None, stage2_url=None, stage3_url=None):
    global s
    s = module
    test_replan_validation()
    test_preview_changes_nothing()
    test_optimisation_cases()
    test_planning_limits()
    test_apply()
    test_apply_series_and_concurrency()
    test_series_amend()
    test_amend_cutoff()
    test_closures_on_adoption()
    test_roundtrip()
    if stage3_url:
        test_upgrade_stage3(stage3_url.rstrip("/"))
    else:
        print("note: no stage-3 URL given; stage-3 upgrade check skipped")
    for label, url, pairs in (("stage-1", stage1_url, False), ("stage-2", stage2_url, True)):
        if url:
            test_upgrade_early(label, url.rstrip("/"), pairs)
