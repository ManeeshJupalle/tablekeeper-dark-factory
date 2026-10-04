#!/usr/bin/env python3
"""Browser checks for the Tablekeeper stage-2 screens, driven with Playwright.

Usage: python3 tests/ui_check.py http://127.0.0.1:8080

Covers every data-testid in stage-2.md, the out-of-order search rule, the lost
response and retry rule, the 409 rule, combination cells, signed-out booking, the
lookup/cancel flow, layout at 375 px and desktop (no horizontal page scroll) and
that every request the browser makes goes to the service itself.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
import urllib.request
import uuid
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright

BASE = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://127.0.0.1:8080"
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
ADA = {"id": "u_ada", "email": "ada@example.com", "password": "correct horse", "display_name": "Ada"}
BOB = {"id": "u_bob", "email": "bob@example.com", "password": "correct horse", "display_name": "Bob"}
FAILS: list[str] = []
PASSES = 0


def check(name, cond, detail=""):
    global PASSES
    if cond:
        PASSES += 1
    else:
        FAILS.append(name)
        print(f"FAIL {name}: {detail}")


def sel(name):
    return f"[data-testid='{name}']"


def api(method, path, body=None, token=None, key=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if key:
        headers["Idempotency-Key"] = key
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None,
                                 method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, (json.loads(r.read() or b"null"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def restaurant(**over):
    r = {"id": "r_anker", "name": "Zum Anker", "timezone": "Europe/Berlin", "slot_minutes": 30,
         "reservation_duration_minutes": 90, "cancellation_cutoff_minutes": 120,
         "opening_hours": [{"weekday": d, "opens": "18:00", "closes": "23:00"} for d in WEEKDAYS],
         "tables": [{"id": "t_1", "label": "1", "capacity": 2}, {"id": "t_2", "label": "2", "capacity": 4},
                    {"id": "t_3", "label": "3", "capacity": 6}],
         "combinable": [["t_1", "t_2"]]}
    r.update(over)
    return r


def reset(**over):
    fx = {"users": [ADA, BOB], "restaurants": [restaurant()], "reservations": []}
    fx.update(over)
    status, _ = api("POST", "/_test/reset", fx)
    assert status == 204, status


DATE = (dt.datetime.now(ZoneInfo("Europe/Berlin")).date() + dt.timedelta(days=7)).isoformat()


def log_in(page, email=ADA["email"], password=ADA["password"]):
    page.goto("/login")
    page.fill(sel("login-email"), email)
    page.fill(sel("login-password"), password)
    page.click(sel("login-submit"))
    page.wait_for_selector(sel("current-user"))


def search(page, party=4, date=DATE, restaurant_id="r_anker"):
    page.goto("/")
    page.select_option(sel("restaurant-select"), restaurant_id)
    page.fill(sel("date-input"), date)
    page.fill(sel("party-size-input"), str(party))
    page.click(sel("search-button"))
    page.wait_for_selector(f"{sel('availability-grid')}, {sel('no-slots')}")


def no_horizontal_scroll(page):
    return page.evaluate("() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1")


def run(browser, viewport, label):
    external = []
    ctx = browser.new_context(base_url=BASE, viewport=viewport)
    ctx.set_default_timeout(10_000)
    ctx.on("request", lambda req: external.append(req.url) if not req.url.startswith(BASE) else None)
    page = ctx.new_page()

    # Routes, auth, header
    reset()
    for route, anchor in (("/", "search-button"), ("/signup", "signup-submit"), ("/login", "login-submit"), ("/lookup", "lookup-submit")):
        page.goto(route)
        page.wait_for_selector(sel(anchor), state="attached")
        check(f"{label} {route} loads", True)
        check(f"{label} {route} no horizontal scroll", no_horizontal_scroll(page))
        check(f"{label} {route} no auth-error when none", page.query_selector(sel("auth-error")) is None)
        check(f"{label} {route} no current-user signed out", page.query_selector(sel("current-user")) is None)
    page.goto("/signup")
    page.fill(sel("signup-email"), "dee@example.com")
    page.fill(sel("signup-password"), "correct horse")
    page.fill(sel("signup-display-name"), "Dee")
    page.click(sel("signup-submit"))
    page.wait_for_selector(sel("current-user"))
    check(f"{label} signup shows current-user", "Dee" in page.text_content(sel("current-user")))
    page.goto("/lookup")
    page.wait_for_selector(sel("current-user"))
    check(f"{label} current-user on every screen", "Dee" in page.text_content(sel("current-user")))
    page.click(sel("logout-button"))
    page.wait_for_selector(sel("current-user"), state="detached")
    page.goto("/login")
    page.fill(sel("login-email"), ADA["email"])
    page.fill(sel("login-password"), "wrong password")
    page.click(sel("login-submit"))
    page.wait_for_selector(sel("auth-error"))
    check(f"{label} bad login auth-error", page.text_content(sel("auth-error")).strip() != "")
    check(f"{label} bad login no current-user", page.query_selector(sel("current-user")) is None)

    # Grid, cells, combination cells
    search(page, party=2)
    for at in ("18:00", "21:30"):
        for t in ("t_1", "t_2", "t_3"):
            check(f"{label} cell {t} {at}", page.query_selector(sel(f"slot-{t}-{at}")) is not None)
    check(f"{label} combo cell present", page.get_attribute(sel("slot-t_1+t_2-19:00"), "data-available") == "true")
    check(f"{label} grid no horizontal scroll", no_horizontal_scroll(page))
    search(page, party=6)
    check(f"{label} t_1 false for 6", page.get_attribute(sel("slot-t_1-19:00"), "data-available") == "false")
    check(f"{label} t_3 true for 6", page.get_attribute(sel("slot-t_3-19:00"), "data-available") == "true")
    check(f"{label} combo true for 6", page.get_attribute(sel("slot-t_1+t_2-19:00"), "data-available") == "true")
    search(page, party=7)
    check(f"{label} combo false for 7", page.get_attribute(sel("slot-t_1+t_2-19:00"), "data-available") == "false")
    check(f"{label} t_3 false for 7", page.get_attribute(sel("slot-t_3-19:00"), "data-available") == "false")

    # Signed-out click
    search(page, party=4)
    page.click(sel("slot-t_2-19:00"))
    page.wait_for_selector(f"{sel('auth-error')}, {sel('login-submit')}")
    check(f"{label} signed-out click shows auth-error", page.query_selector(sel("booking-form")) is None)

    # Unavailable cell does nothing
    log_in(page)
    search(page, party=6)
    page.click(sel("slot-t_1-19:00"), force=True)
    page.wait_for_timeout(200)
    check(f"{label} unavailable click no form", page.query_selector(sel("booking-form")) is None)

    # Closed day
    wd = WEEKDAYS[dt.date.fromisoformat(DATE).weekday()]
    reset(restaurants=[restaurant(opening_hours=[h for h in restaurant()["opening_hours"] if h["weekday"] != wd])])
    search(page, party=2)
    check(f"{label} closed day no-slots", page.query_selector(sel("no-slots")) is not None and page.query_selector(sel("availability-grid")) is None)
    reset()

    # Booking flow + double submit replay + change = new booking
    log_in(page)
    search(page, party=4)
    page.click(sel("slot-t_2-19:00"))
    page.wait_for_selector(sel("booking-form"))
    summary = page.text_content(sel("booking-summary"))
    check(f"{label} summary has time and label", "19:00" in summary and "2" in summary, summary)
    check(f"{label} party prefilled", page.input_value(sel("booking-party-size")) == "4")
    check(f"{label} form no horizontal scroll", no_horizontal_scroll(page))
    page.click(sel("booking-submit"))
    page.wait_for_selector(sel("confirmation"))
    ref = page.text_content(sel("confirmation-reference")).strip()
    check(f"{label} reference exact", re.fullmatch(r"[A-Z0-9]{6,12}", ref) is not None, ref)
    details = page.text_content(sel("confirmation-details"))
    check(f"{label} details content", all(x in details for x in ("Zum Anker", "2", "19:00")), details)
    check(f"{label} confirmation-tables", "2" in page.text_content(sel("confirmation-tables")))
    page.click(sel("booking-submit"))
    page.wait_for_timeout(600)
    check(f"{label} double submit no error", page.query_selector(sel("booking-error")) is None)
    check(f"{label} double submit same ref", page.text_content(sel("confirmation-reference")).strip() == ref)
    _, token = 0, api("POST", "/auth/login", {"email": ADA["email"], "password": ADA["password"]})[1]["token"]
    confirmed = [r for r in api("GET", "/reservations", token=token)[1]["reservations"] if r["status"] == "confirmed"]
    check(f"{label} one reservation after double submit", len(confirmed) == 1, str(len(confirmed)))
    page.fill(sel("booking-party-size"), "3")
    page.click(sel("booking-submit"))
    page.wait_for_selector(sel("booking-error"))
    check(f"{label} changed form refused with error", page.query_selector(sel("confirmation")) is None)
    check(f"{label} form kept after 409", page.query_selector(sel("booking-form")) is not None and page.input_value(sel("booking-party-size")) == "3")
    page.wait_for_timeout(400)
    check(f"{label} grid refreshed after 409", page.get_attribute(sel("slot-t_2-19:00"), "data-available") == "false")

    # 409 by another client
    reset()
    log_in(page)
    search(page, party=4)
    page.click(sel("slot-t_3-19:00"))
    page.wait_for_selector(sel("booking-form"))
    bob = api("POST", "/auth/login", {"email": BOB["email"], "password": BOB["password"]})[1]["token"]
    st, _ = api("POST", "/reservations", {"restaurant_id": "r_anker", "table_id": "t_3", "starts_at_local": f"{DATE}T19:00", "party_size": 4}, token=bob, key=uuid.uuid4().hex)
    check(f"{label} thief booked", st == 201)
    page.click(sel("booking-submit"))
    page.wait_for_selector(sel("booking-error"))
    check(f"{label} 409 shows error, no confirmation", page.query_selector(sel("confirmation")) is None)
    check(f"{label} 409 keeps form", page.query_selector(sel("booking-form")) is not None)

    # Combination booking through the UI
    search(page, party=6)
    page.click(sel("slot-t_1+t_2-20:30"))
    page.wait_for_selector(sel("booking-form"))
    summary = page.text_content(sel("booking-summary"))
    check(f"{label} combo summary names both tables", "1" in summary and "2" in summary and "20:30" in summary, summary)
    page.click(sel("booking-submit"))
    page.wait_for_selector(sel("confirmation"))
    tables = page.text_content(sel("confirmation-tables"))
    check(f"{label} combo confirmation-tables", "1" in tables and "2" in tables, tables)
    combo_ref = page.text_content(sel("confirmation-reference")).strip()
    search(page, party=2)
    check(f"{label} combo blocks singles", page.get_attribute(sel("slot-t_1-20:30"), "data-available") == "false" and page.get_attribute(sel("slot-t_2-20:30"), "data-available") == "false")

    # Lookup, cancel, free slot
    page.goto("/lookup")
    page.fill(sel("lookup-reference-input"), combo_ref)
    page.click(sel("lookup-submit"))
    page.wait_for_selector(sel("reservation-detail"))
    check(f"{label} lookup status", page.text_content(sel("reservation-status")).strip() == "confirmed")
    rt = page.text_content(sel("reservation-tables"))
    check(f"{label} lookup tables", "1" in rt and "2" in rt, rt)
    check(f"{label} lookup no horizontal scroll", no_horizontal_scroll(page))
    page.click(sel("reservation-cancel-button"))
    page.wait_for_selector(sel("reservation-cancel-button"), state="detached")
    check(f"{label} cancelled status", page.text_content(sel("reservation-status")).strip() == "cancelled")
    search(page, party=2)
    check(f"{label} cancel frees singles", page.get_attribute(sel("slot-t_1-20:30"), "data-available") == "true")
    page.goto("/lookup")
    page.fill(sel("lookup-reference-input"), "ZZZZZZ")
    page.click(sel("lookup-submit"))
    page.wait_for_selector(sel("reservation-error"))
    check(f"{label} unknown ref error", page.query_selector(sel("reservation-detail")) is None)

    # Out-of-order searches. Search A's response is held (not merely delayed) while
    # search B is issued, answered and used to open a booking form; only then is A
    # released. Everything on screen must still describe B.
    harbour = restaurant(id="r_harbour", name="Harbour Light", combinable=[["h_bay", "h_win"]],
                         tables=[{"id": "h_bay", "label": "Bay", "capacity": 4}, {"id": "h_win", "label": "Window", "capacity": 2}])
    reset(restaurants=[restaurant(), harbour])
    log_in(page)
    for how in ("click", "enter"):
        held = []

        def hold_first(route, request, held=held):
            if not held and "restaurant_id=r_anker" in request.url:
                held.append(route)          # never answered until released below
                return
            route.continue_()

        page.route("**/availability**", hold_first)
        page.goto("/")
        page.select_option(sel("restaurant-select"), "r_anker")
        page.fill(sel("date-input"), DATE)
        page.fill(sel("party-size-input"), "4")
        page.click(sel("search-button"))                       # search A, held
        for _ in range(100):
            if held:
                break
            page.wait_for_timeout(50)
        check(f"{label} OOO-{how} A is held", bool(held))
        check(f"{label} OOO-{how} search usable while A in flight", page.is_enabled(sel("search-button")))
        page.select_option(sel("restaurant-select"), "r_harbour")
        page.fill(sel("party-size-input"), "6")
        if how == "click":
            page.click(sel("search-button"), timeout=3000)     # search B
        else:
            page.press(sel("party-size-input"), "Enter")       # search B via the keyboard
        page.wait_for_selector(sel("slot-h_bay+h_win-19:00"), timeout=5000)
        page.click(sel("slot-h_bay+h_win-19:00"))
        page.wait_for_selector(sel("booking-form"))
        held[0].continue_()                                    # A's response now arrives late
        page.wait_for_timeout(1200)
        summary = page.text_content(sel("booking-summary"))
        results_text = page.text_content("#results")
        check(f"{label} OOO-{how} grid still describes B", page.query_selector(sel("slot-t_2-19:00")) is None
              and page.get_attribute(sel("slot-h_bay+h_win-19:00"), "data-available") == "true"
              and page.get_attribute(sel("slot-h_bay-19:00"), "data-available") == "false", results_text[:160])
        check(f"{label} OOO-{how} labels describe B", "Harbour Light" in results_text and "Zum Anker" not in results_text, results_text[:160])
        check(f"{label} OOO-{how} form describes B", "Bay" in summary and "Window" in summary and "19:00" in summary
              and page.input_value(sel("booking-party-size")) == "6", summary)
        page.unroute("**/availability**")
    reset()

    # Lost response: the server commits, the browser never sees the reply.
    log_in(page)
    search(page, party=4)
    page.click(sel("slot-t_2-19:00"))
    page.wait_for_selector(sel("booking-form"))
    lost = {"armed": True}

    def drop_response(route, request):
        if lost["armed"]:
            lost["armed"] = False
            route.fetch()      # the server handles it and commits
            route.abort()      # the browser sees a network failure
        else:
            route.continue_()

    page.route("**/reservations", drop_response)
    page.click(sel("booking-submit"))
    page.wait_for_selector(sel("booking-uncertain"))
    check(f"{label} uncertain nonempty", page.text_content(sel("booking-uncertain")).strip() != "")
    check(f"{label} uncertain no error", page.query_selector(sel("booking-error")) is None)
    check(f"{label} uncertain no confirmation", page.query_selector(sel("confirmation")) is None)
    token = api("POST", "/auth/login", {"email": ADA["email"], "password": ADA["password"]})[1]["token"]
    mine = api("GET", "/reservations", token=token)[1]["reservations"]
    committed = [r for r in mine if r["status"] == "confirmed" and r["starts_at_local"] == f"{DATE}T19:00"]
    check(f"{label} server committed the lost booking", len(committed) == 1, str(len(committed)))
    page.click(sel("booking-submit"))          # retry: same key and body -> 200 replay
    page.wait_for_selector(sel("confirmation"))
    check(f"{label} retry shows original reference", page.text_content(sel("confirmation-reference")).strip() == committed[0]["reference"])
    check(f"{label} retry clears uncertainty", page.query_selector(sel("booking-uncertain")) is None and page.query_selector(sel("booking-error")) is None)
    check(f"{label} still one booking", len([r for r in api("GET", "/reservations", token=token)[1]["reservations"] if r["status"] == "confirmed"]) == 1)
    page.unroute("**/reservations")

    # Upgrade path within the page: export, reset, import, same session + retry identity.
    snapshot = api("GET", "/_test/export")[1]
    reset(restaurants=[restaurant(name="Other")])
    st, _ = api("POST", "/_test/import", snapshot)
    check(f"{label} import ok", st == 204)
    page.click(sel("booking-submit"))
    page.wait_for_timeout(600)
    check(f"{label} replay after import same reference", page.text_content(sel("confirmation-reference")).strip() == committed[0]["reference"] and page.query_selector(sel("booking-error")) is None)
    page.goto("/lookup")
    check(f"{label} still signed in after import", page.query_selector(sel("current-user")) is not None)
    page.fill(sel("lookup-reference-input"), committed[0]["reference"])
    page.click(sel("lookup-submit"))
    page.wait_for_selector(sel("reservation-detail"))
    check(f"{label} lookup after import", page.text_content(sel("reservation-status")).strip() == "confirmed")

    # Stage 3: the grid follows the policy in force for the searched date, and a
    # booking made in the browser carries those terms and a truthful history.
    reset(restaurants=[restaurant(manager_user_ids=["u_ada"])])
    log_in(page)
    token = api("POST", "/auth/login", {"email": ADA["email"], "password": ADA["password"]})[1]["token"]
    st, pol = api("POST", "/restaurants/r_anker/policies", {
        "effective_from": DATE, "slot_minutes": 30, "reservation_duration_minutes": 60, "cancellation_cutoff_minutes": 120,
        "opening_hours": restaurant()["opening_hours"], "capacities": {"t_1": 2, "t_2": 2, "t_3": 6}}, token=token, key=uuid.uuid4().hex)
    check(f"{label} s3 policy published", st == 201 and pol["policy_version"] == 1)
    search(page, party=4)
    check(f"{label} s3 grid follows policy capacity", page.get_attribute(sel("slot-t_2-19:00"), "data-available") == "false"
          and page.get_attribute(sel("slot-t_3-19:00"), "data-available") == "true"
          and page.get_attribute(sel("slot-t_1+t_2-19:00"), "data-available") == "true")
    check(f"{label} s3 seats from policy", "seats 2" in page.text_content(sel("slot-t_2-19:00"))
          and "seats 4" in page.text_content(sel("slot-t_1+t_2-19:00")))
    check(f"{label} s3 grid follows policy duration", page.query_selector(sel("slot-t_3-21:30")) is not None
          and page.query_selector(sel("slot-t_3-22:00")) is not None)
    page.click(sel("slot-t_3-19:00"))
    page.wait_for_selector(sel("booking-form"))
    page.click(sel("booking-submit"))
    page.wait_for_selector(sel("confirmation"))
    ref3 = page.text_content(sel("confirmation-reference")).strip()
    got = api("GET", f"/reservations/{ref3}", token=token)[1]
    check(f"{label} s3 browser booking terms", got["accepted_terms"]["policy_version"] == 1 and got["revision"] == 1
          and got["ends_at"].startswith(f"{DATE}T20:00:00"), json.dumps(got)[:200])
    page.goto("/lookup")
    page.fill(sel("lookup-reference-input"), ref3)
    page.click(sel("lookup-submit"))
    page.wait_for_selector(sel("reservation-cancel-button"))
    page.click(sel("reservation-cancel-button"))
    page.wait_for_selector(sel("reservation-cancel-button"), state="detached")
    hist = api("GET", f"/reservations/{ref3}/history", token=token)[1]["entries"]
    check(f"{label} s3 history after browser cancel", [e["event"] for e in hist] == ["created", "cancelled"] and hist[-1]["revision"] == 2)
    earlier = (dt.date.fromisoformat(DATE) - dt.timedelta(days=1)).isoformat()
    search(page, party=4, date=earlier)
    check(f"{label} s3 earlier date uses fixture rules", page.get_attribute(sel("slot-t_2-19:00"), "data-available") == "true"
          and "seats 4" in page.text_content(sel("slot-t_2-19:00")))

    check(f"{label} zero external requests", not external, str(external[:3]))
    ctx.close()


def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chromium", args=[f"--unsafely-treat-insecure-origin-as-secure={BASE}"])
        run(browser, {"width": 375, "height": 740}, "mobile")
        run(browser, {"width": 1280, "height": 900}, "desktop")
        browser.close()
    print(f"\n{PASSES} checks passed, {len(FAILS)} failed")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
