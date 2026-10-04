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

    # Out-of-order searches: delay the first availability response.
    reset()
    delayed = {"armed": True}

    def slow_first(route, request):
        if delayed["armed"] and "party_size=2" in request.url:
            delayed["armed"] = False
            page.wait_for_timeout(1500)
        route.continue_()

    page.route("**/availability**", slow_first)
    page.goto("/")
    page.select_option(sel("restaurant-select"), "r_anker")
    page.fill(sel("date-input"), DATE)
    page.fill(sel("party-size-input"), "2")
    page.click(sel("search-button"))          # search A (slow)
    page.fill(sel("party-size-input"), "6")
    page.click(sel("search-button"))          # search B (fast)
    page.wait_for_selector(sel("availability-grid"))
    page.wait_for_timeout(2200)                # let A's late response arrive
    check(f"{label} out-of-order grid describes B", page.get_attribute(sel("slot-t_1-19:00"), "data-available") == "false"
          and page.get_attribute(sel("slot-t_3-19:00"), "data-available") == "true")
    page.unroute("**/availability**")

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
