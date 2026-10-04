#!/usr/bin/env python3
"""Tablekeeper stage 4 - browser probes (auditor seat).

The stage-2 screen groups are kept unchanged; UI-POL drives the same screens with a published
stage-3 booking policy (policy-driven grid, capacities, duration, closed days, recovery);
UI-PLAN checks that an applied stage-4 seating plan shows on the grid and the lookup screen.

Usage:
    python3 run_browser_probes.py [--base-url http://127.0.0.1:8080] [--only GROUP,GROUP]

Requires Playwright for Python with Chromium:
    pip install playwright && python -m playwright install chromium

Drives the stage-2 screens in headless Chromium and checks the data-testid contract,
the competing-client recovery rules and the viewport/accessibility rules from the
stage-2 requirements. API calls use the stdlib helpers from run_probes.py (same
directory). Prints one PASS/FAIL line per check; exit status 1 when any check fails.
"""
import argparse
import datetime as dt
import json
import os
import sys
import time
import traceback
import urllib.request
from urllib.parse import urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_probes as rp  # noqa: E402
from run_probes import (check, expect_err, s2_fixture, reset, login, book, book2, must_book, must_book2,  # noqa: E402
                        cancel, list_res, get_res, avail, slot_map, options_at, T, THU, WED, zulu_local, newkey, same_json)

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
except ImportError:  # pragma: no cover
    print("FAIL UI-SETUP playwright is not installed -- pip install playwright && python -m playwright install chromium")
    sys.exit(1)

ROUTES = ["/", "/signup", "/login", "/lookup"]
GROUPS = []
EXTERNAL = []
BROWSER = None


def group(name):
    def deco(fn):
        GROUPS.append((name, fn))
        return fn
    return deco


def host():
    return urlsplit(rp.BASE).netloc


# --------------------------------------------------------------------------- page helpers

def new_page(width=1280, height=900):
    ctx = BROWSER.new_context(viewport={"width": width, "height": height})
    page = ctx.new_page()
    page.set_default_timeout(5000)

    def guard(route):
        u = urlsplit(route.request.url)
        if u.scheme in ("http", "https") and u.netloc != host():
            EXTERNAL.append(route.request.url)
            route.abort()
        else:
            route.continue_()
    page.route("**/*", guard)
    return ctx, page


def sel(name):
    return '[data-testid="%s"]' % name


def tid(page, name):
    return page.locator(sel(name))


def count(page, name):
    return tid(page, name).count()


def visible(page, name):
    loc = tid(page, name)
    return loc.count() > 0 and loc.first.is_visible()


def wait_visible(page, name, timeout=5000):
    try:
        tid(page, name).first.wait_for(state="visible", timeout=timeout)
        return True
    except PWTimeout:
        return False


def wait_gone(page, name, timeout=5000):
    try:
        page.wait_for_function("sel => { const n = document.querySelectorAll(sel); return [...n].every(e => !(e.offsetWidth || e.offsetHeight || e.getClientRects().length)); }",
                               arg=sel(name), timeout=timeout)
        return True
    except PWTimeout:
        return False


def text(page, name):
    loc = tid(page, name)
    return loc.first.inner_text().strip() if loc.count() else None


def goto(page, path):
    return page.goto(rp.BASE + path, wait_until="load")


def ui_login(page, email, password):
    goto(page, "/login")
    tid(page, "login-email").fill(email)
    tid(page, "login-password").fill(password)
    tid(page, "login-submit").click()
    return wait_visible(page, "current-user")


def ui_search(page, rid, date, party, navigate=True, wait=True):
    if navigate:
        goto(page, "/")
    tid(page, "restaurant-select").select_option(rid)
    tid(page, "date-input").fill(rp.D(date))
    tid(page, "party-size-input").fill(str(party))
    tid(page, "search-button").click()
    if wait:
        try:
            page.wait_for_selector(sel("availability-grid") + ", " + sel("no-slots"), timeout=5000)
            return True
        except PWTimeout:
            return False
    return True


def click_unavailable(page, testid):
    """Click a cell expected to be unavailable. A disabled control is compliant ("does nothing"),
    so actionability checks are skipped; the caller asserts that nothing happened."""
    try:
        tid(page, testid).first.click(force=True, timeout=2000)
    except PWTimeout:
        pass
    time.sleep(0.5)


def DAY_OF(n):
    return THU + dt.timedelta(days=n)


def cell(table_ids, hhmm):
    return "slot-%s-%s" % ("+".join(table_ids), hhmm)


def hhmm_of(local):
    return local[11:16]


def api_pair_cells_expected(resp):
    """{(hhmm, (ta, tb)): available} for declared pairs from the API response."""
    out = {}
    for s in resp.json["slots"]:
        avail_pairs = {tuple(o["table_ids"]) for o in s.get("available_options", []) if len(o["table_ids"]) == 2}
        for pair in rp.COMBO_PAIRS:
            out[(hhmm_of(s["starts_at_local"]), tuple(pair))] = tuple(pair) in avail_pairs
    return out


def wait_api_count(token, n, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if len(list_res(token)) == n:
            return True
        time.sleep(0.2)
    return len(list_res(token)) == n


# =========================================================================== groups

@group("UI-ROUTE")
def g_routes():
    reset(s2_fixture())
    for r in ROUTES:
        try:
            resp = urllib.request.urlopen(rp.BASE + r, timeout=5)
            ct = resp.headers.get("Content-Type", "")
            ok = resp.status == 200 and ct.lower().startswith("text/html")
            detail = "status=%s content-type=%s" % (resp.status, ct)
        except Exception as e:  # noqa
            ok, detail = False, repr(e)
        check("UI-1[%s]" % r, "GET %s is 200 text/html" % r, ok, detail)
    ctx, page = new_page()
    before = len(EXTERNAL)
    for r in ROUTES:
        resp = goto(page, r)
        check("UI-1b[%s]" % r, "browser loads %s (200)" % r, resp is not None and resp.status == 200, repr(resp and resp.status))
    check("UI-1c", "loading the four routes made no request to another host", len(EXTERNAL) == before, repr(EXTERNAL[before:][:5]))
    ctx.close()


@group("UI-AUTH")
def g_auth():
    reset(s2_fixture())
    ctx, page = new_page()
    goto(page, "/signup")
    check("UI-4a", "auth-error absent before any error", count(page, "auth-error") == 0, "")
    tid(page, "signup-email").fill("nova@probe.test")
    tid(page, "signup-password").fill("nova-secret-9")
    tid(page, "signup-display-name").fill("Nova Quill")
    tid(page, "signup-submit").click()
    ok = wait_visible(page, "current-user")
    check("UI-2", "signup through the screen signs in and current-user contains the display name", ok and "Nova Quill" in (text(page, "current-user") or ""), repr(text(page, "current-user")))
    for r in ROUTES:
        goto(page, r)
        check("UI-3[%s]" % r, "current-user visible on %s while signed in" % r, visible(page, "current-user") and "Nova Quill" in (text(page, "current-user") or ""), repr(text(page, "current-user")))
    goto(page, "/")
    tid(page, "logout-button").first.click()
    gone = wait_gone(page, "current-user")
    check("UI-3b", "logout-button hides current-user", gone, repr(text(page, "current-user")))
    goto(page, "/login")
    check("UI-4b", "auth-error absent on a fresh login screen", count(page, "auth-error") == 0, "")
    tid(page, "login-email").fill("hana@probe.test")
    tid(page, "login-password").fill("wrong-password")
    tid(page, "login-submit").click()
    check("UI-4c", "wrong password shows auth-error", wait_visible(page, "auth-error"), "")
    check("UI-4d", "wrong password does not sign in", count(page, "current-user") == 0 or not visible(page, "current-user"), "")
    tid(page, "login-password").fill("hana-secret-9")
    tid(page, "login-submit").click()
    check("UI-4e", "correct login shows current-user with the display name", wait_visible(page, "current-user") and "Hana" in (text(page, "current-user") or ""), repr(text(page, "current-user")))
    ctx.close()


@group("UI-GRID")
def g_grid():
    reset(s2_fixture())
    ravi = login("ravi")
    must_book(ravi, "r_combo", "c_d", T(THU, "19:00"), 5)
    must_book2(ravi, "r_combo", ["c_b", "c_c"], T(THU, "21:30"), 6)
    api = avail("r_combo", THU, 4)
    ctx, page = new_page()
    ok = ui_search(page, "r_combo", THU, 4)
    check("UI-5a", "search renders availability-grid", ok and visible(page, "availability-grid"), "")
    probs = []
    for s in api.json["slots"]:
        hh = hhmm_of(s["starts_at_local"])
        for t in rp.combo_restaurant()["tables"]:
            loc = tid(page, cell([t["id"]], hh))
            exp = "true" if t["id"] in s["available_table_ids"] else "false"
            if loc.count() != 1:
                probs.append("%s count=%d" % (cell([t["id"]], hh), loc.count()))
            elif loc.first.get_attribute("data-available") != exp:
                probs.append("%s data-available=%r expected %s" % (cell([t["id"]], hh), loc.first.get_attribute("data-available"), exp))
    check("UI-5", "one cell per table per slot with data-available matching GET /availability (party 4)", not probs, "; ".join(probs[:6]))
    probs = []
    for (hh, pair), av in api_pair_cells_expected(api).items():
        loc = tid(page, cell(list(pair), hh))
        if av:
            if loc.count() != 1 or loc.first.get_attribute("data-available") != "true":
                probs.append("%s expected one cell with true, count=%d" % (cell(list(pair), hh), loc.count()))
        else:
            if loc.count() > 0 and loc.first.get_attribute("data-available") != "false":
                probs.append("%s shown but not false" % cell(list(pair), hh))
        rev = tid(page, cell(list(reversed(pair)), hh))
        if rev.count():
            probs.append("%s uses the wrong (non-combinable) id order" % cell(list(reversed(pair)), hh))
    check("UI-6", "combination cells use combinable order and match available_options (party 4)", not probs, "; ".join(probs[:6]))
    ui_search(page, "r_lumen", WED, 2)
    check("UI-7", "closed day shows no-slots and no availability-grid", visible(page, "no-slots") and count(page, "availability-grid") == 0, "")
    ui_search(page, "r_combo", THU, 4)
    tid(page, cell(["c_c"], "19:00")).first.click()
    time.sleep(0.5)
    went_login = page.url.rstrip("/").endswith("/login")
    check("UI-8b", "signed-out click on an available cell shows auth-error or navigates to /login", visible(page, "auth-error") or went_login, "url=%s" % page.url)
    ctx.close()

    ctx, page = new_page()
    ui_login(page, "hana@probe.test", "hana-secret-9")
    ui_search(page, "r_combo", THU, 4)
    click_unavailable(page, cell(["c_d"], "19:00"))
    check("UI-8a", "clicking an unavailable cell opens no booking-form", count(page, "booking-form") == 0, "")
    tid(page, cell(["c_c"], "19:00")).first.click()
    ok = wait_visible(page, "booking-form")
    check("UI-9a", "clicking an available cell while signed in opens booking-form", ok, "")
    check("UI-9b", "booking-party-size is pre-filled from the search", tid(page, "booking-party-size").first.input_value() == "4", repr(tid(page, "booking-party-size").first.input_value() if count(page, "booking-party-size") else None))
    summ = text(page, "booking-summary") or ""
    check("UI-9c", "booking-summary names the table label and the local start time", "Corner" in summ and "19:00" in summ, repr(summ))
    tid(page, "booking-submit").first.click()
    ok = wait_visible(page, "confirmation")
    hana = login("hana")
    mine = [x for x in list_res(hana) if x.get("starts_at_local") == T(THU, "19:00") and x.get("table_ids") == ["c_c"]]
    ref = mine[0]["reference"] if mine else None
    check("UI-10a", "confirmation shown and confirmation-reference is exactly the API reference", ok and ref is not None and text(page, "confirmation-reference") == ref, "ui=%r api=%r" % (text(page, "confirmation-reference"), ref))
    det = text(page, "confirmation-details") or ""
    check("UI-10b", "confirmation-details contains restaurant name, table label and local start", "Combo House" in det and "Corner" in det and "19:00" in det, repr(det))
    check("UI-10c", "no booking-error or booking-uncertain after success", count(page, "booking-error") == 0 and count(page, "booking-uncertain") == 0, "")
    tid(page, cell(["c_b", "c_a"], "19:00")).first.click()
    ok = wait_visible(page, "booking-form")
    summ = text(page, "booking-summary") or ""
    check("UI-11a", "combination cell opens a form whose booking-summary names both labels", ok and "Bay" in summ and "Alcove" in summ, repr(summ))
    tid(page, "booking-submit").first.click()
    ok = wait_visible(page, "confirmation")
    pair = [x for x in list_res(hana) if x.get("starts_at_local") == T(THU, "19:00") and set(x.get("table_ids", [])) == {"c_a", "c_b"}]
    tabs = text(page, "confirmation-tables") or ""
    check("UI-11b", "combination booking confirmed: reference matches API, confirmation-tables names both labels, API holds both ids",
          ok and pair and text(page, "confirmation-reference") == pair[0]["reference"] and "Bay" in tabs and "Alcove" in tabs, "ui=%r tables=%r api=%r" % (text(page, "confirmation-reference"), tabs, [x.get("reference") for x in pair]))
    ctx.close()


@group("UI-OOO")
def g_out_of_order():
    reset(s2_fixture())
    ctx, page = new_page()
    ui_login(page, "hana@probe.test", "hana-secret-9")
    goto(page, "/")
    held = []

    def handler(route):
        if "restaurant_id=r_lumen" in route.request.url and not held:
            held.append(route)
            return
        route.continue_()
    page.route("**/availability**", handler)
    ui_search(page, "r_lumen", THU, 2, navigate=False, wait=False)
    time.sleep(0.3)
    try:
        ok = ui_search(page, "r_combo", THU, 4, navigate=False)
        why = "held=%d" % len(held)
    except PWTimeout:
        btn = tid(page, "search-button").first
        ok, why = False, "search B could not be started while search A was pending (search-button disabled=%s, text=%r)" % (
            btn.is_disabled(), btn.inner_text().strip())
    check("UI-12a", "search B (r_combo) rendered while search A (r_lumen) is still pending", ok and count(page, cell(["c_c"], "19:00")) == 1 and count(page, cell(["t_quad"], "19:00")) == 0 and len(held) == 1, why)
    if not ok:
        for cid, desc in (("UI-12b", "after A's late response the grid still describes B"),
                          ("UI-12c", "after A's late response the booking form still describes B")):
            check(cid, desc, False, "not reached: " + why)
        for r in held:
            try:
                r.continue_()
            except Exception:  # noqa
                pass
        page.unroute("**/availability**")
        ctx.close()
        return
    tid(page, cell(["c_c"], "19:00")).first.click()
    wait_visible(page, "booking-form")
    try:
        held[0].continue_()
    except Exception:  # noqa
        pass
    time.sleep(1.5)
    summ = text(page, "booking-summary") or ""
    check("UI-12b", "after A's late response the grid still describes B", count(page, cell(["c_c"], "19:00")) == 1 and count(page, cell(["t_quad"], "19:00")) == 0, "")
    check("UI-12c", "after A's late response the booking form still describes B", visible(page, "booking-form") and "Corner" in summ, repr(summ))
    page.unroute("**/availability**")
    ctx.close()


@group("UI-409")
def g_conflict():
    reset(s2_fixture())
    ravi = login("ravi")
    ctx, page = new_page()
    ui_login(page, "hana@probe.test", "hana-secret-9")
    ui_search(page, "r_combo", THU, 4)
    tid(page, cell(["c_c"], "19:00")).first.click()
    wait_visible(page, "booking-form")
    must_book(ravi, "r_combo", "c_c", T(THU, "19:00"), 2)
    tid(page, "booking-submit").first.click()
    ok = wait_visible(page, "booking-error")
    check("UI-13a", "409 after the form opened shows booking-error", ok, "")
    try:
        page.wait_for_function("sel => { const e = document.querySelector(sel); return e && e.getAttribute('data-available') === 'false'; }", arg=sel(cell(["c_c"], "19:00")), timeout=5000)
        refreshed = True
    except PWTimeout:
        refreshed = False
    check("UI-13b", "availability refreshed: the taken cell is now data-available=false", refreshed, repr(tid(page, cell(["c_c"], "19:00")).first.get_attribute("data-available") if count(page, cell(["c_c"], "19:00")) else None))
    check("UI-13c", "booking-form and its party size are preserved", visible(page, "booking-form") and tid(page, "booking-party-size").first.input_value() == "4", "")
    check("UI-13d", "no confirmation for the refused attempt", count(page, "confirmation") == 0 and count(page, "confirmation-reference") == 0, "")
    check("UI-16a", "booking-error and booking-uncertain are not shown together (409 path)", count(page, "booking-uncertain") == 0, "")
    hana = login("hana")
    check("UI-13e", "no reservation was created for the refused attempt", len(list_res(hana)) == 0, "")
    ctx.close()


def lost_response_scenario(cid, reach_server):
    reset(s2_fixture())
    hana = login("hana")
    ctx, page = new_page()
    ui_login(page, "hana@probe.test", "hana-secret-9")
    ui_search(page, "r_combo", THU, 4)
    tid(page, cell(["c_b", "c_a"], "19:00")).first.click()
    wait_visible(page, "booking-form")
    first = {}
    server = {}

    def lose(route):
        if route.request.method != "POST":
            route.continue_()
            return
        first["key"] = route.request.headers.get("idempotency-key")
        first["body"] = route.request.post_data
        if reach_server:
            try:
                resp = route.fetch()
                server["status"] = resp.status
                server["json"] = resp.json()
            except Exception as e:  # noqa
                server["error"] = repr(e)
        route.abort("failed")
    page.route("**/reservations", lose)
    tid(page, "booking-submit").first.click()
    ok = wait_visible(page, "booking-uncertain")
    unc = text(page, "booking-uncertain") or ""
    check(cid + "a", "lost response%s shows nonempty booking-uncertain" % (" after commit" if reach_server else " before reaching the server"), ok and unc.strip() != "", repr(unc))
    check(cid + "b", "no booking-error and no confirmation while uncertain", count(page, "booking-error") == 0 and count(page, "confirmation") == 0, "")
    n = len(list_res(hana))
    check(cid + "c", "server state after the lost response: %d reservation(s)" % (1 if reach_server else 0), n == (1 if reach_server else 0), "count=%d server=%r" % (n, server))
    page.unroute("**/reservations")
    retry = {}

    def capture(route):
        if route.request.method == "POST" and "key" not in retry:
            retry["key"] = route.request.headers.get("idempotency-key")
            retry["body"] = route.request.post_data
        route.continue_()
    page.route("**/reservations", capture)
    tid(page, "booking-submit").first.click()
    ok = wait_visible(page, "confirmation")
    same_key = retry.get("key") and retry.get("key") == first.get("key")
    try:
        same_body = same_json(json.loads(first.get("body") or "null"), json.loads(retry.get("body") or "null"))
    except ValueError:
        same_body = False
    check(cid + "d", "retry of the unchanged form sends the same Idempotency-Key and the same body", same_key and same_body, "first=%r retry=%r" % (first, retry))
    mine = list_res(hana)
    ref = mine[0]["reference"] if len(mine) == 1 else None
    exp_ref = server.get("json", {}).get("reference") if reach_server else ref
    check(cid + "e", "successful retry shows the original reference and clears the uncertainty", ok and text(page, "confirmation-reference") == exp_ref and count(page, "booking-uncertain") == 0 and count(page, "booking-error") == 0,
          "ui=%r expected=%r" % (text(page, "confirmation-reference"), exp_ref))
    check(cid + "f", "exactly one reservation exists after the retry", len(mine) == 1 and set(mine[0].get("table_ids", [])) == {"c_a", "c_b"}, repr([x.get("reference") for x in mine]))
    page.unroute("**/reservations")
    ctx.close()


@group("UI-LOST")
def g_lost():
    lost_response_scenario("UI-14", True)
    lost_response_scenario("UI-15", False)


@group("UI-RESUBMIT")
def g_resubmit():
    reset(s2_fixture())
    hana = login("hana")
    ctx, page = new_page()
    ui_login(page, "hana@probe.test", "hana-secret-9")
    ui_search(page, "r_combo", THU, 4)
    tid(page, cell(["c_c"], "19:00")).first.click()
    wait_visible(page, "booking-form")
    posts = []
    page.route("**/reservations", lambda route: (posts.append((route.request.headers.get("idempotency-key"), route.request.post_data)) if route.request.method == "POST" else None, route.continue_()))
    tid(page, "booking-submit").first.click()
    wait_visible(page, "confirmation")
    ref1 = text(page, "confirmation-reference")
    tid(page, "booking-submit").first.click()
    time.sleep(1.0)
    n = len(list_res(hana))
    keys = {k for k, _ in posts}
    check("UI-17a", "resubmitting an unchanged successful form keeps the same confirmation-reference, no booking-error, no new booking",
          text(page, "confirmation-reference") == ref1 and count(page, "booking-error") == 0 and n == 1 and len(keys) == 1, "ref=%r now=%r count=%d keys=%r" % (ref1, text(page, "confirmation-reference"), n, keys))
    cancel(hana, list_res(hana)[0]["reference"])
    tid(page, "booking-party-size").first.fill("3")
    tid(page, "booking-submit").first.click()
    try:
        page.wait_for_function("(a) => { const e = document.querySelector(a[0]); return e && e.innerText.trim() !== a[1]; }", arg=[sel("confirmation-reference"), ref1], timeout=5000)
    except PWTimeout:
        pass
    ref2 = text(page, "confirmation-reference")
    mine = list_res(hana)
    keys = [k for k, _ in posts]
    check("UI-17b", "after changing a field the next submission is a new request with a new key and a new reference",
          ref2 is not None and ref2 != ref1 and len(mine) == 2 and len(set(keys)) == 2 and keys[-1] != keys[0] and any(x.get("party_size") == 3 and x.get("status") == "confirmed" for x in mine),
          "ref1=%r ref2=%r keys=%r mine=%r" % (ref1, ref2, keys, [(x.get("reference"), x.get("party_size"), x.get("status")) for x in mine]))
    page.unroute("**/reservations")
    ctx.close()


@group("UI-LOOKUP")
def g_lookup():
    reset(s2_fixture())
    hana = login("hana")
    ravi = login("ravi")
    P = must_book2(hana, "r_combo", ["c_b", "c_a"], T(THU, "19:00"), 4)
    Q = must_book(hana, "r_zulu", "z_1", zulu_local(50), 2)
    R = must_book(ravi, "r_combo", "c_d", T(THU, "19:00"), 2)
    ctx, page = new_page()
    ui_login(page, "hana@probe.test", "hana-secret-9")

    def lookup(ref):
        goto(page, "/lookup")
        tid(page, "lookup-reference-input").fill(ref)
        tid(page, "lookup-submit").click()
        try:
            page.wait_for_selector(sel("reservation-detail") + ", " + sel("reservation-error"), timeout=5000)
        except PWTimeout:
            pass
    lookup(P["reference"])
    tabs = text(page, "reservation-tables") or ""
    check("UI-18a", "lookup of a pair booking shows reservation-detail, status exactly confirmed and both labels",
          visible(page, "reservation-detail") and text(page, "reservation-status") == "confirmed" and "Bay" in tabs and "Alcove" in tabs, "status=%r tables=%r" % (text(page, "reservation-status"), tabs))
    check("UI-18b", "reservation-cancel-button present while confirmed; no reservation-error", visible(page, "reservation-cancel-button") and count(page, "reservation-error") == 0, "")
    tid(page, "reservation-cancel-button").first.click()
    try:
        page.wait_for_function("s => { const e = document.querySelector(s); return e && e.innerText.trim() === 'cancelled'; }", arg=sel("reservation-status"), timeout=5000)
    except PWTimeout:
        pass
    check("UI-18c", "cancel through the screen: status exactly cancelled, button absent, API agrees",
          text(page, "reservation-status") == "cancelled" and count(page, "reservation-cancel-button") == 0 and get_res(hana, P["reference"]).json.get("status") == "cancelled", repr(text(page, "reservation-status")))
    lookup("ZZZZZZ")
    check("UI-19a", "unknown reference shows reservation-error", visible(page, "reservation-error"), "")
    lookup(R["reference"])
    check("UI-19b", "another user's reference shows reservation-error and no detail", visible(page, "reservation-error") and count(page, "reservation-detail") == 0, "")
    lookup(Q["reference"])
    ok = visible(page, "reservation-detail") and text(page, "reservation-status") == "confirmed"
    tid(page, "reservation-cancel-button").first.click()
    ok2 = wait_visible(page, "reservation-error")
    check("UI-19c", "cancel refused by the cutoff shows reservation-error and the status stays confirmed",
          ok and ok2 and text(page, "reservation-status") == "confirmed" and get_res(hana, Q["reference"]).json.get("status") == "confirmed", "status=%r" % text(page, "reservation-status"))
    ctx.close()


@group("UI-UPGRADE")
def g_upgrade_in_browser():
    reset(s2_fixture())
    hana = login("hana")
    ravi = login("ravi")
    K = must_book(hana, "r_combo", "c_d", T(THU, "19:00"), 5)
    ctx, page = new_page()
    ui_login(page, "hana@probe.test", "hana-secret-9")
    ui_search(page, "r_combo", THU, 4)
    tid(page, cell(["c_c"], "19:00")).first.click()
    wait_visible(page, "booking-form")
    server = {}

    def lose(route):
        if route.request.method != "POST":
            route.continue_()
            return
        try:
            resp = route.fetch()
            server["json"] = resp.json()
        except Exception as e:  # noqa
            server["error"] = repr(e)
        route.abort("failed")
    page.route("**/reservations", lose)
    tid(page, "booking-submit").first.click()
    ok = wait_visible(page, "booking-uncertain")
    page.unroute("**/reservations")
    ex = rp.call("GET", "/_test/export", timeout=10)
    cancel(hana, server.get("json", {}).get("reference", "XXXXXX"))
    must_book(ravi, "r_combo", "c_a", T(THU, "19:00"), 2)
    imp = rp.call("POST", "/_test/import", body=ex.json, timeout=10)
    check("UI-20a", "setup: lost response, export, mutations, import", ok and ex.status == 200 and imp.status == 204 and "json" in server, "server=%r imp=%r" % (server, imp))
    check("UI-20b", "after the upgrade import the page (no reload) still shows current-user", visible(page, "current-user") and "Hana" in (text(page, "current-user") or ""), repr(text(page, "current-user")))
    tid(page, "booking-submit").first.click()
    ok = wait_visible(page, "confirmation")
    check("UI-20c", "the pending retry recovers the original confirmation after the import",
          ok and text(page, "confirmation-reference") == server.get("json", {}).get("reference") and count(page, "booking-uncertain") == 0 and count(page, "booking-error") == 0,
          "ui=%r expected=%r" % (text(page, "confirmation-reference"), server.get("json", {}).get("reference")))
    goto(page, "/lookup")
    tid(page, "lookup-reference-input").fill(K["reference"])
    tid(page, "lookup-submit").click()
    ok = wait_visible(page, "reservation-detail")
    check("UI-20d", "a retained reference still works through the lookup screen after the upgrade", ok and text(page, "reservation-status") == "confirmed" and visible(page, "current-user"), repr(text(page, "reservation-status")))
    ctx.close()


@group("UI-POL")
def g_policy_screens():
    """Stage 3: the stage-2 screens with a published policy that changes the grid, capacities and duration."""
    reset(rp.s3_fixture())
    mgr = rp.login3("mgr")
    hana = login("hana")
    P = rp.must_publish(mgr, rp.policy(THU, slot=60, dur=120, oh=rp.hours(rp.ALL_DAYS, "17:00", "22:00"),
                                       caps={"p_a": 3, "p_b": 4, "p_c": 6}))
    rp.must_book(login("ravi"), "r_pol", "p_c", T(THU, "19:00"), 2)
    api = avail("r_pol", THU, 3)
    ctx, page = new_page()
    ui_login(page, "hana@probe.test", "hana-secret-9")
    ok = ui_search(page, "r_pol", THU, 3)
    probs = []
    for s in api.json["slots"]:
        hh = hhmm_of(s["starts_at_local"])
        for t in ("p_a", "p_b", "p_c"):
            loc = tid(page, cell([t], hh))
            exp = "true" if t in s["available_table_ids"] else "false"
            if loc.count() != 1:
                probs.append("%s count=%d" % (cell([t], hh), loc.count()))
            elif loc.first.get_attribute("data-available") != exp:
                probs.append("%s data-available=%r expected %s" % (cell([t], hh), loc.first.get_attribute("data-available"), exp))
    check("UI-P1", "with a published policy the grid has one cell per table per policy slot and data-available matches GET /availability",
          ok and [hhmm_of(s["starts_at_local"]) for s in api.json["slots"]] == ["17:00", "18:00", "19:00", "20:00"] and not probs, "; ".join(probs[:6]))
    check("UI-P2", "no cell exists for a time the policy's 60-minute grid does not offer (17:30, 18:30)",
          count(page, cell(["p_a"], "17:30")) == 0 and count(page, cell(["p_b"], "18:30")) == 0, "")
    check("UI-P3", "p_a is offered to a party of 3 because the policy raises its capacity to 3",
          tid(page, cell(["p_a"], "17:00")).first.get_attribute("data-available") == "true", "")
    check("UI-P3b", "a table booked by another diner is unavailable at the overlapping policy slots (p_c 18:00 and 19:00 false, 17:00 true)",
          [tid(page, cell(["p_c"], h)).first.get_attribute("data-available") for h in ("17:00", "18:00", "19:00")] == ["true", "false", "false"], "")
    pair = tid(page, cell(["p_b", "p_a"], "17:00"))
    check("UI-P4", "the declared pair cell uses combinable order and is available (policy capacities 4 + 3)",
          pair.count() == 1 and pair.first.get_attribute("data-available") == "true" and count(page, cell(["p_a", "p_b"], "17:00")) == 0, "")
    tid(page, cell(["p_a"], "17:00")).first.click()
    wait_visible(page, "booking-form")
    summ = text(page, "booking-summary") or ""
    check("UI-P5", "the booking form names the table label and the policy slot time; party size is pre-filled", "Arbour" in summ and "17:00" in summ and tid(page, "booking-party-size").first.input_value() == "3", repr(summ))
    tid(page, "booking-submit").first.click()
    ok = wait_visible(page, "confirmation")
    mine = [x for x in list_res(hana) if x.get("starts_at_local") == T(THU, "17:00")]
    ref = mine[0]["reference"] if mine else None
    check("UI-P6", "confirmation-reference is exactly the API reference of the booking made under the policy",
          ok and ref is not None and text(page, "confirmation-reference") == ref, "ui=%r api=%r" % (text(page, "confirmation-reference"), ref))
    check("UI-P6b", "the booking accepted the policy's terms (version 1, 120-minute end)",
          bool(mine) and (mine[0].get("accepted_terms") or {}).get("policy_version") == 1 and rp.minutes_between(mine[0]["starts_at"], mine[0]["ends_at"]) == 120, repr(mine[:1]))
    goto(page, "/lookup")
    tid(page, "lookup-reference-input").fill(ref or "XXXXXX")
    tid(page, "lookup-submit").click()
    wait_visible(page, "reservation-detail")
    check("UI-P7", "lookup of that booking shows status confirmed and the table label",
          text(page, "reservation-status") == "confirmed" and "Arbour" in (text(page, "reservation-tables") or ""), repr(text(page, "reservation-status")))
    ctx.close()

    ctx, page = new_page(width=375, height=800)
    ui_login(page, "hana@probe.test", "hana-secret-9")
    ui_search(page, "r_pol", THU, 2)
    sw, cw = page.evaluate("[document.scrollingElement.scrollWidth, document.scrollingElement.clientWidth]")
    check("UI-P8", "no horizontal scrolling at 375 px with the policy-driven grid", visible(page, "availability-grid") and sw <= cw, "scrollWidth=%s clientWidth=%s" % (sw, cw))
    tid(page, cell(["p_b"], "18:00")).first.click()
    wait_visible(page, "booking-form")
    first, server = {}, {}

    def lose(route):
        if route.request.method != "POST":
            route.continue_()
            return
        first["key"] = route.request.headers.get("idempotency-key")
        first["body"] = route.request.post_data
        try:
            resp = route.fetch()
            server["json"] = resp.json()
        except Exception as e:  # noqa
            server["error"] = repr(e)
        route.abort("failed")
    page.route("**/reservations", lose)
    tid(page, "booking-submit").first.click()
    ok = wait_visible(page, "booking-uncertain")
    page.unroute("**/reservations")
    retry = {}

    def capture(route):
        if route.request.method == "POST" and "key" not in retry:
            retry["key"] = route.request.headers.get("idempotency-key")
            retry["body"] = route.request.post_data
        route.continue_()
    page.route("**/reservations", capture)
    tid(page, "booking-submit").first.click()
    ok2 = wait_visible(page, "confirmation")
    page.unroute("**/reservations")
    try:
        same_body = same_json(json.loads(first.get("body") or "null"), json.loads(retry.get("body") or "null"))
    except ValueError:
        same_body = False
    check("UI-P9", "a lost response under the policy shows booking-uncertain; the retry reuses key and body and recovers the original reference",
          ok and ok2 and first.get("key") and first.get("key") == retry.get("key") and same_body
          and text(page, "confirmation-reference") == (server.get("json") or {}).get("reference") and count(page, "booking-uncertain") == 0,
          "first=%r retry=%r server=%r ui=%r" % (first, retry, server, text(page, "confirmation-reference")))
    n = [x for x in list_res(hana) if x.get("starts_at_local") == T(THU, "18:00")]
    check("UI-P9b", "exactly one booking exists for the retried request", len(n) == 1, repr([x.get("reference") for x in n]))
    ctx.close()

    rp.must_publish(mgr, rp.policy(DAY_OF(7), oh=[{"weekday": rp.ALL_DAYS[DAY_OF(8).weekday()], "opens": "18:00", "closes": "23:00"}]))
    ctx, page = new_page()
    ui_login(page, "hana@probe.test", "hana-secret-9")
    ui_search(page, "r_pol", DAY_OF(7), 2)
    check("UI-P10", "a day the selected policy closes shows no-slots instead of the grid", visible(page, "no-slots") and count(page, "availability-grid") == 0, "")
    ctx.close()


@group("UI-PLAN")
def g_plan_screens():
    """Stage 4: an applied seating plan is reflected on the availability grid and the lookup screen."""
    reset(rp.s4_fixture())
    mgr = rp.login3("mgr")
    hana = login("hana")
    B = rp.must_book(hana, "r_combo", "c_b", T(THU, "19:00"), 2)
    p = rp.must_plan(mgr, "r_combo", "c_b", rp.inst(THU, "19:00"), rp.inst(THU, "20:00"))
    rp.must_apply(mgr, "r_combo", p["plan_id"])
    for width in (1280, 375):
        ctx, page = new_page(width=width, height=800)
        ui_login(page, "hana@probe.test", "hana-secret-9")
        ok = ui_search(page, "r_combo", THU, 2)
        api = avail("r_combo", THU, 2)
        probs = []
        for s in api.json["slots"]:
            hh = hhmm_of(s["starts_at_local"])
            for t in ("c_a", "c_b", "c_c", "c_d"):
                loc = tid(page, cell([t], hh))
                exp = "true" if t in s["available_table_ids"] else "false"
                if loc.count() != 1 or loc.first.get_attribute("data-available") != exp:
                    probs.append("%s %r expected %s" % (cell([t], hh), loc.first.get_attribute("data-available") if loc.count() else None, exp))
        check("UI-L1[%d]" % width, "after the plan the grid matches GET /availability at %d px" % width, ok and not probs, "; ".join(probs[:6]))
        closed = [tid(page, cell(["c_b"], h)).first.get_attribute("data-available") for h in ("18:00", "18:30", "19:00", "19:30")]
        check("UI-L2[%d]" % width, "the closed table's cells are unavailable for every slot overlapping the closure" , closed == ["false"] * 4, repr(closed))
        pairs = [tid(page, cell(["c_b", x], h)) for x in ("c_a", "c_c") for h in ("18:00", "19:00", "19:30")]
        check("UI-L3[%d]" % width, "pair cells containing the closed table are absent or unavailable during the closure",
              all(c.count() == 0 or c.first.get_attribute("data-available") == "false" for c in pairs), "")
        check("UI-L4[%d]" % width, "the moved booking's old table is free again once the closure ends (c_b 20:00 true) and its new table is taken (c_a 19:00 false)",
              tid(page, cell(["c_b"], "20:00")).first.get_attribute("data-available") == "true"
              and tid(page, cell(["c_a"], "19:00")).first.get_attribute("data-available") == "false", "")
        sw, cw = page.evaluate("[document.scrollingElement.scrollWidth, document.scrollingElement.clientWidth]")
        check("UI-L5[%d]" % width, "no horizontal scrolling at %d px with the plan applied" % width, sw <= cw, "scrollWidth=%s clientWidth=%s" % (sw, cw))
        goto(page, "/lookup")
        tid(page, "lookup-reference-input").fill(B["reference"])
        tid(page, "lookup-submit").click()
        ok = wait_visible(page, "reservation-detail")
        tabs = text(page, "reservation-tables") or ""
        check("UI-L6[%d]" % width, "lookup of the moved booking shows status confirmed and the new table (Alcove), not the closed one (Bay)",
              ok and text(page, "reservation-status") == "confirmed" and "Alcove" in tabs and "Bay" not in tabs, repr(tabs))
        ctx.close()
    ctx, page = new_page()
    ui_login(page, "hana@probe.test", "hana-secret-9")
    ui_search(page, "r_combo", THU, 2)
    tid(page, cell(["c_b"], "20:00")).first.click()
    wait_visible(page, "booking-form")
    tid(page, "booking-submit").first.click()
    ok = wait_visible(page, "confirmation")
    mine = [x for x in list_res(hana) if x.get("starts_at_local") == T(THU, "20:00") and x.get("table_ids") == ["c_b"]]
    check("UI-L7", "booking the freed table right after the closure works through the screens (confirmation reference equals the API)",
          ok and bool(mine) and text(page, "confirmation-reference") == mine[0]["reference"], repr(text(page, "confirmation-reference")))
    ctx.close()


@group("UI-VIEW")
def g_viewport():
    reset(s2_fixture())
    for width in (375, 1280):
        ctx, page = new_page(width=width, height=800)
        ui_login(page, "hana@probe.test", "hana-secret-9")
        for r in ROUTES:
            goto(page, r)
            sw, cw = page.evaluate("[document.scrollingElement.scrollWidth, document.scrollingElement.clientWidth]")
            check("UI-21[%d,%s]" % (width, r), "no horizontal scrolling at %dpx on %s" % (width, r), sw <= cw, "scrollWidth=%s clientWidth=%s" % (sw, cw))
            missing = page.evaluate("""() => {
                const vis = e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
                const out = [];
                for (const el of document.querySelectorAll('input, select, textarea')) {
                    if (!vis(el) || el.type === 'hidden' || el.type === 'submit' || el.type === 'button') continue;
                    const labelled = [...el.labels || []].some(vis) || el.getAttribute('aria-label') || el.getAttribute('aria-labelledby') || el.closest('label');
                    if (!labelled) out.push(el.getAttribute('data-testid') || el.id || el.name || el.outerHTML.slice(0, 60));
                }
                return out; }""")
            check("UI-22[%d,%s]" % (width, r), "every visible input has a visible label on %s at %dpx" % (r, width), not missing, repr(missing))
        ui_search(page, "r_combo", THU, 2)
        sw, cw = page.evaluate("[document.scrollingElement.scrollWidth, document.scrollingElement.clientWidth]")
        check("UI-21[%d,grid]" % width, "no horizontal scrolling at %dpx with a rendered grid" % width, visible(page, "availability-grid") and sw <= cw, "scrollWidth=%s clientWidth=%s" % (sw, cw))
        ctx.close()

    ctx, page = new_page()
    ui_login(page, "hana@probe.test", "hana-secret-9")
    goto(page, "/")
    page.evaluate("document.body.focus && document.activeElement && document.activeElement.blur()")
    checked, bad = 0, []
    for _ in range(8):
        page.keyboard.press("Tab")
        info = page.evaluate("""() => {
            const el = document.activeElement; if (!el || el === document.body) return null;
            const style = e => { const s = getComputedStyle(e); return [s.outlineStyle, s.outlineWidth, s.outlineColor, s.boxShadow, s.borderColor, s.backgroundColor, s.textDecorationLine].join('|'); };
            const focused = style(el); el.blur(); const blurred = style(el); el.focus();
            return {id: el.getAttribute('data-testid') || el.id || el.tagName, focused, blurred}; }""")
        if not info:
            continue
        checked += 1
        if info["focused"] == info["blurred"]:
            bad.append(info["id"])
    check("UI-23", "keyboard focus is visible on tabbed controls (computed style changes on focus)", checked >= 3 and not bad, "checked=%d no-visible-focus=%r" % (checked, bad))
    ravi = login("ravi")
    must_book(ravi, "r_combo", "c_d", T(THU, "19:00"), 5)
    ui_search(page, "r_combo", THU, 4)
    tid(page, cell(["c_c"], "19:00")).first.click()
    wait_visible(page, "booking-form")
    styles = page.evaluate("""(sels) => sels.map(s => { const e = document.querySelector(s); if (!e) return null;
        const c = getComputedStyle(e); return [c.backgroundColor, c.color, c.borderColor, c.borderWidth, c.opacity, c.textDecorationLine, c.outlineStyle, c.boxShadow, c.fontWeight].join('|'); })""",
                           [sel(cell(["c_c"], "19:00")), sel(cell(["c_c"], "20:30")), sel(cell(["c_d"], "19:00"))])
    selected, available, unavailable = styles
    check("UI-24", "available, unavailable and selected cells have distinguishable computed styles",
          all(styles) and len({selected, available, unavailable}) == 3, "selected=%r available=%r unavailable=%r" % (selected, available, unavailable))
    held = []

    def hold(route):
        if not held:
            held.append(route)
            return
        route.continue_()
    page.route("**/availability**", hold)
    ui_search(page, "r_combo", THU, 2, navigate=False, wait=False)
    time.sleep(0.4)
    loading = page.evaluate("""() => {
        const vis = e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
        if ([...document.querySelectorAll('[aria-busy="true"], [data-testid*="loading" i], [role="status"], [role="progressbar"], .loading, [data-loading]')].some(vis)) return 'element';
        const b = document.querySelector('[data-testid="search-button"]'); if (b && (b.disabled || b.getAttribute('aria-disabled') === 'true')) return 'button-disabled';
        if (/loading|searching|looking/i.test(document.body.innerText || '')) return 'text';
        return null; }""")
    check("UI-25", "a loading indicator exists while a search is pending (held availability response)", loading is not None, "no aria-busy/loading testid/status role/disabled search button/loading text found")
    try:
        held[0].continue_()
    except Exception:  # noqa
        pass
    page.unroute("**/availability**")
    ctx.close()


# =========================================================================== main

def main():
    global BROWSER
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", default=rp.BASE)
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    rp.BASE = args.base_url.rstrip("/")
    only = {x.strip().upper() for x in args.only.split(",") if x.strip()}
    print("# tablekeeper stage-4 browser probes against %s (THU=%s)" % (rp.BASE, THU))
    with sync_playwright() as pw:
        BROWSER = pw.chromium.launch(headless=True)
        for name, fn in GROUPS:
            if only and name not in only:
                continue
            print("## %s" % name)
            try:
                fn()
            except Exception:
                check(name + "-EXC", "group ran without probe exception", False, traceback.format_exc().strip().splitlines()[-1])
        BROWSER.close()
    check("UI-1d", "no browser flow in the whole run contacted a host other than the service", not EXTERNAL, repr(EXTERNAL[:5]))
    check("UI-CONV", "no 5xx was observed by the API helpers during the browser run", not rp.SERVER_FAULTS, repr(rp.SERVER_FAULTS[:3]))
    print("# %d passed, %d failed" % (rp.PASSED, rp.FAILED))
    sys.exit(1 if rp.FAILED else 0)


if __name__ == "__main__":
    main()
