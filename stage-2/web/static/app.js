/* Tablekeeper browser app (stage 2). Plain JavaScript, no dependencies.
 *
 * Rules that matter beyond looks:
 *  - Searches are numbered; a response that is not for the latest search is dropped.
 *  - A booking form owns one Idempotency-Key for as long as its fields are unchanged.
 *    Resubmitting an unchanged form replays; editing any field mints a new key.
 *  - A lost booking response leaves the form in an "uncertain" state and the next
 *    submit retries with the same key and body. The server stays authoritative.
 */
(() => {
  'use strict';

  const SESSION_KEY = 'tablekeeper.session';

  // ---- small DOM helpers -------------------------------------------------
  const $ = (sel, root) => (root || document).querySelector(sel);

  function el(tag, attrs, children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === 'text') node.textContent = v;
      else if (k === 'class') node.className = v;
      else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v === true ? '' : v);
    }
    for (const child of [].concat(children || [])) {
      if (child === null || child === undefined || child === false) continue;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }

  function clear(node) { while (node && node.firstChild) node.removeChild(node.firstChild); }

  function newKey() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    const bytes = new Uint8Array(16);
    (window.crypto || {}).getRandomValues ? crypto.getRandomValues(bytes) : bytes.forEach((_, i) => { bytes[i] = Math.floor(Math.random() * 256); });
    return Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('');
  }

  // ---- session -----------------------------------------------------------
  function session() {
    try { return JSON.parse(localStorage.getItem(SESSION_KEY)) || null; } catch (e) { return null; }
  }
  function saveSession(s) { localStorage.setItem(SESSION_KEY, JSON.stringify(s)); }
  function clearSession() { localStorage.removeItem(SESSION_KEY); }

  // ---- API ---------------------------------------------------------------
  async function api(method, path, opts) {
    opts = opts || {};
    const headers = { Accept: 'application/json' };
    if (opts.body !== undefined) headers['Content-Type'] = 'application/json';
    if (opts.token) headers.Authorization = 'Bearer ' + opts.token;
    if (opts.key) headers['Idempotency-Key'] = opts.key;
    const res = await fetch(path, {
      method, headers, cache: 'no-store',
      body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
    });
    const text = await res.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch (e) { data = null; }
    return { status: res.status, data };
  }

  const MESSAGES = {
    table_unavailable: 'Sorry, that seating was just taken. The grid has been refreshed, so pick another time or table.',
    party_exceeds_capacity: 'That seating cannot hold a party of this size. Choose a bigger table or a combined pair.',
    combination_not_allowed: 'Those tables cannot be combined at this restaurant.',
    not_on_slot_grid: 'That time is not one of the bookable slots.',
    outside_opening_hours: 'The restaurant is not open for that time.',
    invalid_local_time: 'That local time does not exist on this date (clocks change).',
    validation_failed: 'Please check the details and try again.',
    unauthenticated: 'Your session is no longer valid. Please log in again.',
    not_found: 'We could not find that. Check the details and try again.',
    cutoff_passed: 'It is too close to the start time to change or cancel this reservation.',
    reservation_cancelled: 'This reservation has already been cancelled.',
    email_taken: 'That email already has an account. Try logging in instead.',
    idempotency_key_reuse: 'This request no longer matches the one already sent. Please start again.',
  };
  function describe(result, fallback) {
    const code = result && result.data && result.data.error && result.data.error.code;
    return MESSAGES[code] || fallback || 'Something went wrong. Please try again.';
  }

  // ---- header ------------------------------------------------------------
  function renderHeader() {
    const slot = $('#auth-slot');
    if (!slot) return;
    clear(slot);
    const s = session();
    if (s) {
      slot.append(
        el('span', { class: 'current-user', 'data-testid': 'current-user' }, ['Signed in as ', el('strong', { text: s.display_name || s.user_id })]),
        el('button', { type: 'button', class: 'btn btn-secondary btn-small', 'data-testid': 'logout-button', onclick: () => { clearSession(); renderHeader(); document.dispatchEvent(new CustomEvent('tk:session')); } }, 'Log out'),
      );
    } else {
      slot.append(
        el('a', { href: '/login', class: 'btn btn-secondary btn-small', text: 'Log in' }),
        el('a', { href: '/signup', class: 'btn btn-primary btn-small', text: 'Sign up' }),
      );
    }
  }

  // ---- formatting --------------------------------------------------------
  function friendlyDate(iso) {
    const [y, m, d] = iso.split('-').map(Number);
    return new Date(y, m - 1, d).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' });
  }
  function timeOf(startsAtLocal) { return startsAtLocal.slice(11, 16); }
  function dateOf(startsAtLocal) { return startsAtLocal.slice(0, 10); }
  function tableLabel(rest, id) {
    const t = (rest.tables || []).find(x => x.id === id);
    return t && t.label ? t.label : id;
  }
  function tablesTitle(rest, ids) {
    const labels = ids.map(id => tableLabel(rest, id));
    return (ids.length === 1 ? 'Table ' : 'Tables ') + labels.join(' + ');
  }
  function capacityOf(rest, ids) {
    return ids.reduce((sum, id) => { const t = (rest.tables || []).find(x => x.id === id); return sum + (t ? t.capacity : 0); }, 0);
  }
  function whenText(startsAtLocal) { return friendlyDate(dateOf(startsAtLocal)) + ' at ' + timeOf(startsAtLocal); }

  // ======================================================================
  // Search screen
  // ======================================================================
  function initSearch() {
    const form = $('#search-form');
    const results = $('#results');
    const panel = $('#booking-panel');
    const dateInput = $('#date');
    if (!dateInput.value) {
      const now = new Date();
      dateInput.value = [now.getFullYear(), String(now.getMonth() + 1).padStart(2, '0'), String(now.getDate()).padStart(2, '0')].join('-');
    }

    let seq = 0;                // latest search number
    let current = null;         // { params, restaurant, availability }
    let booking = null;         // open booking form state

    form.addEventListener('submit', (e) => { e.preventDefault(); runSearch(readParams(), false); });
    document.addEventListener('tk:session', () => { if (booking) renderBooking(); });

    function readParams() {
      return { restaurantId: $('#restaurant').value, date: dateInput.value, party: $('#party').value.trim() };
    }

    // A search never blocks the next one: the diner can change their mind while a
    // response is outstanding. Every search is numbered and any response that is
    // not for the latest one is dropped, so a late answer cannot restore old results.
    async function runSearch(params, keepForm) {
      const mine = ++seq;
      const submit = $('[data-testid="search-button"]', form);
      if (!keepForm) { booking = null; current = null; clear(panel); }
      if (!params.restaurantId || !params.date || !params.party) {
        setBusy(submit, false);
        clear(results);
        results.append(el('p', { class: 'notice notice-error', role: 'alert', text: 'Please choose a restaurant, a date and a party size.' }));
        return;
      }
      setBusy(submit, true);
      if (!keepForm) { clear(results); results.append(el('p', { class: 'loading', role: 'status', text: 'Checking availability…' })); }
      try {
        const q = '/availability?restaurant_id=' + encodeURIComponent(params.restaurantId) + '&date=' + encodeURIComponent(params.date) + '&party_size=' + encodeURIComponent(params.party);
        const [rest, avail] = await Promise.all([api('GET', '/restaurants/' + encodeURIComponent(params.restaurantId)), api('GET', q)]);
        if (mine !== seq) return; // a newer search owns the screen
        if (rest.status !== 200 || avail.status !== 200) {
          clear(results);
          results.append(el('p', { class: 'notice notice-error', role: 'alert', text: avail.status === 200 ? describe(rest) : describe(avail, 'We could not load availability for that search.') }));
          return;
        }
        current = { params, restaurant: rest.data, availability: avail.data };
        renderResults();
      } catch (err) {
        if (mine !== seq) return;
        clear(results);
        results.append(el('p', { class: 'notice notice-error', role: 'alert', text: 'We could not reach the server. Check your connection and try again.' }));
      } finally {
        if (mine === seq) setBusy(submit, false);
      }
    }

    // Loading is shown on the results and on the button, which stays clickable.
    function setBusy(submit, busy) {
      submit.classList.toggle('is-busy', busy);
      submit.textContent = busy ? 'Checking…' : 'Check availability';
      if (busy) results.setAttribute('aria-busy', 'true');
      else results.removeAttribute('aria-busy');
    }

    function renderResults() {
      const { restaurant, availability, params } = current;
      clear(results);
      const party = Number(params.party);
      results.append(el('p', { class: 'results-meta' }, [
        el('strong', { text: restaurant.name || restaurant.id }), ' · ', friendlyDate(availability.date), ' · party of ', String(party),
        ' · all times are local to the restaurant (', restaurant.timezone, ')',
      ]));
      if (!availability.slots.length) {
        results.append(el('div', { class: 'empty-state', 'data-testid': 'no-slots' }, [
          el('p', { text: (restaurant.name || 'This restaurant') + ' has no seatings on ' + friendlyDate(availability.date) + '.' }),
          el('p', { class: 'muted', text: 'Try another date.' }),
        ]));
        return;
      }
      const grid = el('div', { class: 'grid', 'data-testid': 'availability-grid', role: 'list' });
      const pairs = restaurant.combinable || [];
      for (const slot of availability.slots) {
        const hhmm = timeOf(slot.starts_at_local);
        const options = el('div', { class: 'slot-options' });
        for (const t of restaurant.tables) {
          options.append(cell([t.id], hhmm, slot.available_table_ids.includes(t.id), slot));
        }
        if (pairs.length) {
          options.append(el('span', { class: 'group-label', text: 'Combined tables' }));
          for (const p of pairs) {
            const open = (slot.available_options || []).some(o => o.table_ids.length === 2 && o.table_ids[0] === p[0] && o.table_ids[1] === p[1]);
            options.append(cell([p[0], p[1]], hhmm, open, slot));
          }
        }
        grid.append(el('div', { class: 'slot-row', role: 'listitem' }, [el('div', { class: 'slot-time', text: hhmm }), options]));
      }
      results.append(grid);
      markSelected();
    }

    function cell(ids, hhmm, available, slot) {
      const rest = current.restaurant;
      const selected = booking && booking.startsAtLocal === slot.starts_at_local && booking.tableIds.join('+') === ids.join('+');
      const btn = el('button', {
        type: 'button',
        class: 'cell' + (ids.length > 1 ? ' is-combo' : '') + (selected ? ' is-selected' : ''),
        'data-testid': 'slot-' + ids.join('+') + '-' + hhmm,
        'data-available': available ? 'true' : 'false',
        'data-slot': slot.starts_at_local,
        'data-tables': ids.join('+'),
        'aria-pressed': selected ? 'true' : 'false',
        disabled: !available,
        title: available ? 'Reserve ' + tablesTitle(rest, ids) + ' at ' + hhmm : tablesTitle(rest, ids) + ' is not available at ' + hhmm,
        onclick: () => { if (available) openBooking(ids, slot); },
      }, [
        el('span', { class: 'cell-label', text: tablesTitle(rest, ids) }),
        el('span', { class: 'cell-meta', text: (available ? 'seats ' : 'unavailable · seats ') + capacityOf(rest, ids) }),
      ]);
      return btn;
    }

    function markSelected() {
      for (const c of results.querySelectorAll('.cell')) {
        const on = !!booking && c.getAttribute('data-slot') === booking.startsAtLocal && c.getAttribute('data-tables') === booking.tableIds.join('+');
        c.classList.toggle('is-selected', on);
        c.setAttribute('aria-pressed', on ? 'true' : 'false');
      }
    }

    function openBooking(ids, slot) {
      if (!session()) {
        booking = null;
        clear(panel);
        panel.append(el('div', { class: 'card booking-card' }, [
          el('h2', { text: 'Log in to reserve' }),
          el('p', { class: 'notice notice-error', role: 'alert', 'data-testid': 'auth-error' }, ['Please ', el('a', { href: '/login', text: 'log in' }), ' or ', el('a', { href: '/signup', text: 'create an account' }), ' to reserve ' + tablesTitle(current.restaurant, ids) + ' at ' + timeOf(slot.starts_at_local) + '.']),
        ]));
        markSelected();
        return;
      }
      booking = {
        restaurantId: current.restaurant.id,
        restaurant: current.restaurant,
        tableIds: ids.slice(),
        startsAtLocal: slot.starts_at_local,
        party: String(Number(current.params.party)),
        key: newKey(),
        inFlight: false,
        confirmation: null,
        error: null,
        uncertain: null,
      };
      renderBooking();
      markSelected();
      const first = $('[data-testid="booking-party-size"]', panel);
      if (window.matchMedia('(min-width: 900px)').matches) {
        if (first) first.focus();
      } else {
        panel.scrollIntoView({ behavior: 'smooth', block: 'start' }); // the form sits below the grid on small screens
      }
    }

    function renderBooking() {
      clear(panel);
      if (!booking) return;
      const b = booking;
      const rest = b.restaurant;
      const partyInput = el('input', {
        type: 'number', min: '1', step: '1', inputmode: 'numeric', id: 'booking-party', value: b.party,
        'data-testid': 'booking-party-size',
        oninput: (e) => {
          // Any edit is a new booking request: fresh key, previous outcome no longer applies.
          b.party = e.target.value;
          b.key = newKey();
          b.confirmation = null; b.error = null; b.uncertain = null;
          renderOutcome();
        },
      });
      const submit = el('button', { type: 'submit', class: 'btn btn-primary btn-block', 'data-testid': 'booking-submit', text: b.inFlight ? 'Confirming…' : (b.uncertain ? 'Retry confirmation' : 'Confirm reservation'), disabled: b.inFlight });
      const formEl = el('form', { class: 'card booking-card', 'data-testid': 'booking-form', novalidate: true, onsubmit: submitBooking }, [
        el('p', { class: 'eyebrow', text: 'Your selection' }),
        el('h2', { text: 'Reserve ' + tablesTitle(rest, b.tableIds) }),
        el('p', { class: 'summary', 'data-testid': 'booking-summary' }, [
          el('span', { class: 'summary-tables', text: tablesTitle(rest, b.tableIds) + ' · seats ' + capacityOf(rest, b.tableIds) }),
          el('span', { class: 'summary-when', text: (rest.name || rest.id) + ' · ' + whenText(b.startsAtLocal) }),
          el('span', { class: 'summary-raw', text: b.startsAtLocal.replace('T', ' ') + ' local time' }),
        ]),
        el('div', { class: 'field' }, [el('label', { for: 'booking-party', text: 'Party size' }), partyInput]),
        el('div', { id: 'booking-outcome' }),
        submit,
      ]);
      panel.append(formEl);
      renderOutcome();
    }

    function renderOutcome() {
      const out = $('#booking-outcome', panel);
      if (!out || !booking) return;
      clear(out);
      const b = booking;
      if (b.error) out.append(el('p', { class: 'notice notice-error', role: 'alert', 'data-testid': 'booking-error', text: b.error }));
      if (b.uncertain) out.append(el('p', { class: 'notice notice-warn', role: 'status', 'data-testid': 'booking-uncertain', text: b.uncertain }));
      if (b.inFlight) out.append(el('p', { class: 'loading', text: 'Confirming with the restaurant…' }));
      const old = $('[data-testid="confirmation"]', panel);
      if (old) old.remove();
      if (b.confirmation) panel.append(renderConfirmation(b.confirmation, b.restaurant));
      const submit = $('[data-testid="booking-submit"]', panel);
      if (submit) { submit.disabled = b.inFlight; submit.textContent = b.inFlight ? 'Confirming…' : (b.uncertain ? 'Retry confirmation' : 'Confirm reservation'); }
    }

    function renderConfirmation(c, rest) {
      const ids = c.table_ids || (c.table_id ? [c.table_id] : []);
      return el('section', { class: 'confirmation', 'data-testid': 'confirmation', 'aria-label': 'Reservation confirmed' }, [
        el('p', { class: 'eyebrow', text: 'Reservation confirmed' }),
        el('p', { class: 'reference', 'data-testid': 'confirmation-reference', text: c.reference }),
        el('p', { 'data-testid': 'confirmation-details' }, [
          el('strong', { text: rest.name || rest.id }), ' · ', tablesTitle(rest, ids), ' · ', whenText(c.starts_at_local),
          el('span', { class: 'confirmation-raw', text: ' (' + c.starts_at_local.replace('T', ' ') + ', party of ' + c.party_size + ')' }),
        ]),
        el('p', { 'data-testid': 'confirmation-tables', text: tablesTitle(rest, ids) }),
        el('p', { class: 'muted', text: 'Keep the reference handy; you can review or cancel the reservation from "Your reservation".' }),
      ]);
    }

    async function submitBooking(e) {
      e.preventDefault();
      const b = booking;
      if (!b || b.inFlight) return;
      const s = session();
      if (!s) { b.error = 'Please log in to reserve.'; renderOutcome(); return; }
      const party = Number(b.party);
      if (!Number.isInteger(party) || party < 1) { b.error = 'Party size must be a whole number of at least 1.'; renderOutcome(); return; }
      const body = { restaurant_id: b.restaurantId, table_ids: b.tableIds, starts_at_local: b.startsAtLocal, party_size: party };
      b.inFlight = true; b.error = null;
      renderOutcome();
      let result;
      try {
        result = await api('POST', '/reservations', { body, token: s.token, key: b.key });
      } catch (err) {
        // The request may or may not have reached the server. Keep key and body for a retry.
        if (booking !== b) return;
        b.inFlight = false;
        b.uncertain = 'We could not tell whether the reservation went through. Nothing has been lost: press "Retry confirmation" and we will check with the restaurant using the same request.';
        renderOutcome();
        return;
      }
      if (booking !== b) return;
      b.inFlight = false;
      if ((result.status === 201 || result.status === 200) && result.data && result.data.reference) {
        b.confirmation = result.data; b.error = null; b.uncertain = null;
        renderOutcome();
        if (current) runSearch(current.params, true); // the taken seating greys out in the grid
        return;
      }
      if (result.status >= 500 || result.data === null) {
        b.uncertain = 'The restaurant did not answer clearly. Press "Retry confirmation" to check again with the same request.';
        renderOutcome();
        return;
      }
      // A definite refusal.
      b.uncertain = null;
      b.error = describe(result);
      const code = result.data.error && result.data.error.code;
      if (code === 'unauthenticated') { clearSession(); renderHeader(); }
      renderOutcome();
      if (code === 'table_unavailable' && current) runSearch(current.params, true);
    }
  }

  // ======================================================================
  // Signup and login
  // ======================================================================
  function initAuth(kind) {
    const form = $('#' + kind + '-form');
    const messages = $('#auth-messages');
    const status = $('#auth-status');
    showSignedIn();
    document.addEventListener('tk:session', showSignedIn);

    // The form stays usable while signed in, so switching accounts is one step.
    function showSignedIn() {
      clear(status);
      const s = session();
      if (!s) return;
      status.append(el('div', { class: 'notice notice-success' }, [
        el('p', {}, ['You are signed in as ', el('strong', { text: s.display_name || s.user_id }), '. ', kind === 'signup' ? 'Creating another account will switch to it.' : 'Logging in below switches to that account.']),
        el('p', { class: 'reservation-actions' }, [
          el('a', { href: '/', class: 'btn btn-primary btn-small', text: 'Find a table' }),
          el('a', { href: '/lookup', class: 'btn btn-secondary btn-small', text: 'Look up a reservation' }),
        ]),
      ]));
    }

    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      clear(messages);
      const email = $('[data-testid="' + kind + '-email"]').value.trim();
      const password = $('[data-testid="' + kind + '-password"]').value;
      const body = { email, password };
      if (kind === 'signup') body.display_name = $('[data-testid="signup-display-name"]').value.trim();
      const submit = $('[data-testid="' + kind + '-submit"]');
      submit.disabled = true;
      // While the request is in flight nobody is "signed in" yet: the header shows
      // progress instead of a previous session, so a successful sign-in is only
      // announced once the new session is actually stored.
      const slot = $('#auth-slot');
      clear(slot);
      slot.append(el('span', { class: 'current-user', text: kind === 'signup' ? 'Creating account…' : 'Signing in…' }));
      try {
        const result = await api('POST', kind === 'signup' ? '/auth/signup' : '/auth/login', { body });
        if ((result.status === 201 || result.status === 200) && result.data && result.data.token) {
          saveSession({ token: result.data.token, display_name: result.data.display_name, user_id: result.data.user_id });
          renderHeader();
          showSignedIn();
          return;
        }
        const fallback = kind === 'login' ? 'That email and password do not match an account.' : 'We could not create the account. Check the details and try again.';
        const code = result.data && result.data.error && result.data.error.code;
        const text = code === 'unauthenticated' ? fallback
          : code === 'validation_failed' ? (kind === 'signup' ? 'Enter a valid email address and a password of at least 8 characters.' : 'Enter your email and password.')
          : describe(result, fallback);
        messages.append(el('p', { class: 'notice notice-error', role: 'alert', 'data-testid': 'auth-error', text }));
      } catch (err) {
        messages.append(el('p', { class: 'notice notice-error', role: 'alert', 'data-testid': 'auth-error', text: 'We could not reach the server. Check your connection and try again.' }));
      } finally {
        submit.disabled = false;
        renderHeader();
      }
    });
  }

  // ======================================================================
  // Lookup
  // ======================================================================
  function initLookup() {
    const form = $('#lookup-form');
    const out = $('#lookup-result');
    let viewing = null; // { reservation, restaurant }

    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const reference = $('[data-testid="lookup-reference-input"]').value.trim().toUpperCase();
      clear(out);
      viewing = null;
      if (!reference) { showError('Enter the reservation reference from your confirmation.'); return; }
      const s = session();
      if (!s) { showError('Please log in to look up a reservation.', true); return; }
      out.append(el('p', { class: 'loading', text: 'Finding your reservation…' }));
      try {
        const res = await api('GET', '/reservations/' + encodeURIComponent(reference), { token: s.token });
        clear(out);
        if (res.status !== 200 || !res.data) {
          showError(res.status === 404 ? 'No reservation with reference ' + reference + ' is linked to your account.' : describe(res));
          if (res.status === 401) { clearSession(); renderHeader(); }
          return;
        }
        const restRes = await api('GET', '/restaurants/' + encodeURIComponent(res.data.restaurant_id));
        viewing = { reservation: res.data, restaurant: restRes.status === 200 ? restRes.data : { id: res.data.restaurant_id, name: res.data.restaurant_id, tables: [] } };
        render();
      } catch (err) {
        clear(out);
        showError('We could not reach the server. Check your connection and try again.');
      }
    });

    function showError(text, withLogin) {
      const notice = el('p', { class: 'notice notice-error', role: 'alert', 'data-testid': 'reservation-error' }, [text]);
      if (withLogin) notice.append(' ', el('a', { href: '/login', text: 'Log in' }), '.');
      out.append(notice);
    }

    function render(extraError) {
      clear(out);
      const { reservation: r, restaurant: rest } = viewing;
      const ids = r.table_ids || (r.table_id ? [r.table_id] : []);
      const confirmed = r.status === 'confirmed';
      const detail = el('section', { class: 'reservation', 'data-testid': 'reservation-detail', 'aria-label': 'Reservation ' + r.reference }, [
        el('div', { class: 'reservation-head' }, [
          el('span', { class: 'reservation-ref', text: r.reference }),
          el('span', { class: 'status status-' + r.status, 'data-testid': 'reservation-status', text: r.status }),
        ]),
        el('ul', { class: 'detail-list' }, [
          el('li', {}, [el('span', { class: 'k', text: 'Restaurant' }), el('span', { class: 'v', text: rest.name || rest.id })]),
          el('li', {}, [el('span', { class: 'k', text: ids.length > 1 ? 'Tables' : 'Table' }), el('span', { class: 'v', 'data-testid': 'reservation-tables', text: tablesTitle(rest, ids) })]),
          el('li', {}, [el('span', { class: 'k', text: 'When' }), el('span', { class: 'v', text: whenText(r.starts_at_local) + ' (' + r.starts_at_local.replace('T', ' ') + ' local)' })]),
          el('li', {}, [el('span', { class: 'k', text: 'Party' }), el('span', { class: 'v', text: r.party_size + (r.party_size === 1 ? ' guest' : ' guests') })]),
        ]),
      ]);
      if (extraError) detail.append(el('p', { class: 'notice notice-error', role: 'alert', 'data-testid': 'reservation-error', text: extraError }));
      if (confirmed) {
        detail.append(el('div', { class: 'reservation-actions' }, [
          el('button', { type: 'button', class: 'btn btn-danger', 'data-testid': 'reservation-cancel-button', onclick: cancel, text: 'Cancel reservation' }),
        ]));
      } else {
        detail.append(el('p', { class: 'notice notice-info', text: 'This reservation is cancelled. The table has been released.' }));
      }
      out.append(detail);
    }

    async function cancel(e) {
      const btn = e.currentTarget;
      btn.disabled = true; btn.textContent = 'Cancelling…';
      const s = session();
      if (!s) { render('Please log in again to cancel.'); return; }
      try {
        const res = await api('POST', '/reservations/' + encodeURIComponent(viewing.reservation.reference) + '/cancel', { token: s.token });
        if (res.status === 200 && res.data) { viewing.reservation = res.data; render(); return; }
        render(describe(res, 'The reservation could not be cancelled.'));
      } catch (err) {
        render('We could not reach the server. The reservation may still be active; please try again.');
      }
    }
  }

  // ---- boot ----------------------------------------------------------------
  renderHeader();
  const page = document.body.getAttribute('data-page');
  if (page === 'search') initSearch();
  else if (page === 'signup') initAuth('signup');
  else if (page === 'login') initAuth('login');
  else if (page === 'lookup') initLookup();
})();
