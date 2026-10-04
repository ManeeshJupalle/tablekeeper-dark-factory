package main

import (
	"encoding/json"
	"net/http"
	"sort"
	"time"
	"unicode/utf8"
)

const (
	maxIdempotencyKeyLength = 255
	pathReservations        = "/reservations"
)

type reservationView struct {
	ReservationID string    `json:"reservation_id"`
	Reference     string    `json:"reference"`
	RestaurantID  string    `json:"restaurant_id"`
	TableIDs      []string  `json:"table_ids"`
	TableID       *string   `json:"table_id,omitempty"` // only for a single-table set
	PartySize     int       `json:"party_size"`
	Status        string    `json:"status"`
	StartsAtLocal string    `json:"starts_at_local"`
	StartsAt      string    `json:"starts_at"`
	EndsAt        string    `json:"ends_at"`
	CreatedAt     string    `json:"created_at"`
	Revision      int       `json:"revision"`
	AcceptedTerms termsView `json:"accepted_terms"`
}

func viewOf(res *Reservation) reservationView {
	v := reservationView{
		ReservationID: res.ID,
		Reference:     res.Reference,
		RestaurantID:  res.RestaurantID,
		TableIDs:      append([]string{}, res.TableIDs...),
		PartySize:     res.PartySize,
		Status:        res.Status,
		StartsAtLocal: res.StartsAtLocal.String(),
		StartsAt:      formatTime(res.StartsAt),
		EndsAt:        formatTime(res.endsAt()),
		CreatedAt:     formatTime(res.CreatedAt.UTC()),
		Revision:      res.Revision,
		AcceptedTerms: termsViewOf(res.Terms),
	}
	if len(res.TableIDs) == 1 {
		v.TableID = &res.TableIDs[0]
	}
	return v
}

// bookingFields are the amendable parts of a booking as sent by a client.
// A nil value means the field was absent.
type bookingFields struct {
	restaurantID *string
	tableIDs     []string
	startsAt     *localTime
	partySize    *int
}

// parseTableSet reads `table_ids`, or the stage-1 `table_id` meaning a set of
// one. Wrong JSON types are 400; sending both or an empty list is 422.
func parseTableSet(m map[string]any) ([]string, *apiError) {
	single, hasSingle, ok := stringField(m, "table_id")
	if !ok {
		return nil, malformed("table_id must be a string")
	}
	raw, hasSet := m["table_ids"]
	if hasSingle && hasSet {
		return nil, invalid("send either table_id or table_ids, not both")
	}
	if hasSingle {
		return []string{single}, nil
	}
	if !hasSet {
		return nil, nil
	}
	arr, ok := raw.([]any)
	if !ok {
		return nil, malformed("table_ids must be an array of strings")
	}
	ids := make([]string, 0, len(arr))
	for _, v := range arr {
		s, ok := v.(string)
		if !ok {
			return nil, malformed("table_ids must be an array of strings")
		}
		ids = append(ids, s)
	}
	if len(ids) == 0 {
		return nil, invalid("table_ids must name at least one table")
	}
	return ids, nil
}

// parseBookingFields validates field presence and type. requireAll is the POST
// rule (every field required); PATCH and moves accept any subset.
func parseBookingFields(m map[string]any, requireAll bool) (bookingFields, *apiError) {
	var f bookingFields
	if requireAll {
		s, present, ok := stringField(m, "restaurant_id")
		if !ok {
			return f, malformed("restaurant_id must be a string")
		}
		if !present {
			return f, invalid("restaurant_id is required")
		}
		f.restaurantID = &s
	}
	ids, e := parseTableSet(m)
	if e != nil {
		return f, e
	}
	if ids == nil && requireAll {
		return f, invalid("table_ids is required")
	}
	f.tableIDs = ids
	if v, present := m["starts_at_local"]; present {
		s, ok := v.(string)
		if !ok {
			return f, invalid("starts_at_local must be a string")
		}
		lt, ok := parseLocalTime(s)
		if !ok {
			return f, invalid("starts_at_local must be a bare local time YYYY-MM-DDTHH:MM")
		}
		f.startsAt = &lt
	} else if requireAll {
		return f, invalid("starts_at_local is required")
	}
	if v, present := m["party_size"]; present {
		n, ok := jsonInt(v)
		if !ok || n < 1 || n > int64(int(^uint(0)>>1)) {
			return f, invalid("party_size must be an integer of at least 1")
		}
		p := int(n)
		f.partySize = &p
	} else if requireAll {
		return f, invalid("party_size is required")
	}
	return f, nil
}

// placement is a fully resolved booking request, checked against everything
// except table occupancy. tableIDs is in canonical (fixture/combinable) order and
// terms are the policy selected for the local start date.
type placement struct {
	tableIDs  []string
	local     localTime
	startsAt  time.Time
	partySize int
	terms     *Terms
}

func (p placement) endsAt() time.Time { return p.startsAt.Add(p.terms.duration()) }

// currentPlacement describes a booking as it stands, with its own accepted terms.
func currentPlacement(res *Reservation) placement {
	return placement{tableIDs: res.TableIDs, local: res.StartsAtLocal, startsAt: res.StartsAt, partySize: res.PartySize, terms: res.Terms}
}

// checkDistinct rejects a table set that names a table twice.
func checkDistinct(ids []string) *apiError {
	seen := map[string]bool{}
	for _, id := range ids {
		if seen[id] {
			return invalid("duplicate table id in the set")
		}
		seen[id] = true
	}
	return nil
}

// resolveTableSet validates a requested table set against the restaurant: no
// duplicates, at most a pair, every table known, and a pair declared combinable.
// It returns the set in canonical order.
func resolveTableSet(rest *Restaurant, ids []string) ([]string, *apiError) {
	if e := checkDistinct(ids); e != nil {
		return nil, e
	}
	if len(ids) > maxTablesPerBooking {
		return nil, fail(http.StatusUnprocessableEntity, codeCombinationNotAllowed, "at most two tables may be combined")
	}
	for _, id := range ids {
		if rest.table(id) == nil {
			return nil, notFound("no such table at this restaurant")
		}
	}
	if len(ids) == 1 {
		return []string{ids[0]}, nil
	}
	p := rest.pair(ids[0], ids[1])
	if p == nil {
		return nil, fail(http.StatusUnprocessableEntity, codeCombinationNotAllowed, "those tables are not declared combinable")
	}
	return []string{p[0], p[1]}, nil
}

// resolvePlacement applies the booking rules in order: the table set is valid,
// the local time exists, and under the policy for that local date the start is
// inside opening hours, on the grid, ends by closing, and the party fits.
func resolvePlacement(rest *Restaurant, ids []string, lt localTime, party int) (placement, *apiError) {
	set, e := resolveTableSet(rest, ids)
	if e != nil {
		return placement{}, e
	}
	start, ok := resolveLocal(rest.Location, lt)
	if !ok {
		return placement{}, fail(http.StatusUnprocessableEntity, codeInvalidLocalTime, "that local time does not exist on this date")
	}
	terms := rest.termsFor(lt.localDate)
	if e := terms.checkHours(lt); e != nil {
		return placement{}, e
	}
	if party > terms.capacity(set) {
		return placement{}, fail(http.StatusUnprocessableEntity, codeExceedsCapacity, "party_size exceeds the seating capacity")
	}
	return placement{tableIDs: set, local: lt, startsAt: start, partySize: party, terms: terms}, nil
}

// tableBusy reports whether a confirmed reservation other than those in exclude
// occupies the table during the half-open interval [start, end).
func (s *State) tableBusy(rest *Restaurant, tableID string, start, end time.Time, exclude map[string]bool) bool {
	return s.setBusy(rest, []string{tableID}, start, end, exclude)
}

// setBusy reports whether any table of the set is occupied during [start, end).
// Each existing booking occupies its tables for its own accepted duration.
func (s *State) setBusy(rest *Restaurant, ids []string, start, end time.Time, exclude map[string]bool) bool {
	for _, res := range s.reservations {
		if res.Status != statusConfirmed || res.RestaurantID != rest.ID || exclude[res.ID] || !sharesTable(res.TableIDs, ids) {
			continue
		}
		if res.StartsAt.Before(end) && start.Before(res.endsAt()) {
			return true
		}
	}
	return false
}

// cutoffPassed applies the booking's accepted cutoff against its current start.
func cutoffPassed(res *Reservation, at time.Time) bool {
	return !at.Before(res.StartsAt.Add(-res.Terms.cutoff()))
}

// idempotencyKey validates the header (§7). Caller holds the lock.
func idempotencyKey(r *http.Request) (string, *apiError) {
	key := r.Header.Get("Idempotency-Key")
	if key == "" {
		return "", fail(http.StatusBadRequest, codeMissingKey, "Idempotency-Key header is required")
	}
	if utf8.RuneCountInString(key) > maxIdempotencyKeyLength {
		return "", invalid("Idempotency-Key must be 1 to 255 characters")
	}
	return key, nil
}

// replay answers a repeated request from its record: true when it was handled.
func replay(w http.ResponseWriter, st *State, k idemKey, canonical string) bool {
	rec := st.idem[k]
	if rec == nil {
		return false
	}
	if rec.Body != canonical {
		writeError(w, fail(http.StatusConflict, codeKeyReuse, "Idempotency-Key was already used with a different body"))
		return true
	}
	writeRaw(w, http.StatusOK, rec.Response)
	return true
}

var tableUnavailable = fail(http.StatusConflict, codeTableUnavailable, "a table is taken for an overlapping interval")

// newReservation creates a confirmed booking at revision 1 with its created entry.
func (s *State) newReservation(user *User, rest *Restaurant, p placement, at time.Time) *Reservation {
	res := &Reservation{
		ID:            s.newReservationID(),
		Reference:     s.newReference(),
		UserID:        user.ID,
		RestaurantID:  rest.ID,
		TableIDs:      p.tableIDs,
		PartySize:     p.partySize,
		Status:        statusConfirmed,
		StartsAtLocal: p.local,
		StartsAt:      p.startsAt,
		CreatedAt:     at,
		Terms:         p.terms,
		Revision:      initialRevision,
	}
	res.record(eventCreated, at, creationChanges(res))
	s.addReservation(res)
	return res
}

func (a *app) createReservation(w http.ResponseWriter, r *http.Request) {
	body, e := readBody(w, r, maxBodyBytes)
	if e != nil {
		writeError(w, e)
		return
	}
	a.store.mu.Lock()
	defer a.store.mu.Unlock()
	st := a.store.state
	m, e := parseJSONObject(body)
	if e != nil {
		writeError(w, e)
		return
	}
	user, e := authenticate(st, r)
	if e != nil {
		writeError(w, e)
		return
	}
	key, e := idempotencyKey(r)
	if e != nil {
		writeError(w, e)
		return
	}
	k := idemKey{user.ID, http.MethodPost, pathReservations, key}
	canonical := canonicalJSON(m)
	if replay(w, st, k, canonical) {
		return
	}

	f, e := parseBookingFields(m, true)
	if e != nil {
		writeError(w, e)
		return
	}
	rest := st.restaurantsByID[*f.restaurantID]
	if rest == nil {
		writeError(w, notFound("no such restaurant"))
		return
	}
	p, e := resolvePlacement(rest, f.tableIDs, *f.startsAt, *f.partySize)
	if e != nil {
		writeError(w, e)
		return
	}
	if st.setBusy(rest, p.tableIDs, p.startsAt, p.endsAt(), nil) {
		writeError(w, tableUnavailable)
		return
	}
	res := st.newReservation(user, rest, p, stamp())
	rest.Revision++
	resp, _ := json.Marshal(viewOf(res))
	st.idem[k] = &IdemRecord{idemKey: k, Body: canonical, Status: http.StatusCreated, Response: resp}
	writeRaw(w, http.StatusCreated, resp)
}

func (a *app) listReservations(w http.ResponseWriter, r *http.Request) {
	a.store.mu.Lock()
	defer a.store.mu.Unlock()
	st := a.store.state
	user, e := authenticate(st, r)
	if e != nil {
		writeError(w, e)
		return
	}
	mine := []*Reservation{}
	for _, res := range st.reservations {
		if res.UserID == user.ID {
			mine = append(mine, res)
		}
	}
	sort.SliceStable(mine, func(i, j int) bool { return mine[i].StartsAt.After(mine[j].StartsAt) })
	out := []reservationView{}
	for _, res := range mine {
		out = append(out, viewOf(res))
	}
	writeJSON(w, http.StatusOK, map[string]any{"reservations": out})
}

// ownReservation finds the caller's booking by reference; anyone else's is 404.
func ownReservation(st *State, user *User, reference string) (*Reservation, *Restaurant, *apiError) {
	res := st.reservationByRf[reference]
	if res == nil || res.UserID != user.ID {
		return nil, nil, notFound("no such reservation")
	}
	return res, st.restaurantsByID[res.RestaurantID], nil
}

func (a *app) getReservation(w http.ResponseWriter, r *http.Request, reference string) {
	a.store.mu.Lock()
	defer a.store.mu.Unlock()
	st := a.store.state
	user, e := authenticate(st, r)
	if e != nil {
		writeError(w, e)
		return
	}
	res, _, e := ownReservation(st, user, reference)
	if e != nil {
		writeError(w, e)
		return
	}
	writeJSON(w, http.StatusOK, viewOf(res))
}

func (a *app) cancelReservation(w http.ResponseWriter, r *http.Request, reference string) {
	a.store.mu.Lock()
	defer a.store.mu.Unlock()
	st := a.store.state
	user, e := authenticate(st, r)
	if e != nil {
		writeError(w, e)
		return
	}
	res, rest, e := ownReservation(st, user, reference)
	if e != nil {
		writeError(w, e)
		return
	}
	if res.Status == statusCancelled {
		writeJSON(w, http.StatusOK, viewOf(res))
		return
	}
	if cutoffPassed(res, time.Now()) {
		writeError(w, fail(http.StatusConflict, codeCutoffPassed, "the cancellation cutoff has passed"))
		return
	}
	res.Status = statusCancelled
	res.Revision++
	res.record(eventCancelled, stamp(), []Change{})
	rest.Revision++
	if sr := st.seriesByID[res.SeriesID]; sr != nil {
		sr.Revision++ // retained, not an exception
	}
	writeJSON(w, http.StatusOK, viewOf(res))
}

// checkExpectedRevision applies the optional expected_revision of an amendment:
// invalid type or range is 422, a different revision is 409 stale_revision.
func checkExpectedRevision(m map[string]any, res *Reservation) *apiError {
	v, present := m["expected_revision"]
	if !present {
		return nil
	}
	n, ok := jsonInt(v)
	if !ok || n < 1 {
		return invalid("expected_revision must be a positive integer")
	}
	if n != int64(res.Revision) {
		return fail(http.StatusConflict, codeStaleRevision, "the reservation has changed since that revision")
	}
	return nil
}

// checkEditable applies the state checks every amendment shares, in order:
// expected revision, cancelled, accepted cutoff against the current start.
func checkEditable(m map[string]any, res *Reservation, at time.Time) *apiError {
	if e := checkExpectedRevision(m, res); e != nil {
		return e
	}
	if res.Status == statusCancelled {
		return fail(http.StatusConflict, codeCancelled, "a cancelled reservation cannot be changed")
	}
	if cutoffPassed(res, at) {
		return fail(http.StatusConflict, codeCutoffPassed, "the amendment cutoff has passed")
	}
	return nil
}

// planAmendment resolves a requested change. It reports changed=false for a
// no-op (every supplied value equals the current one, a reversed pair included),
// which keeps the booking's terms; otherwise the result is validated in full
// against the policy of the resulting start date. Occupancy is left to the caller.
func planAmendment(rest *Restaurant, res *Reservation, f bookingFields) (bool, placement, *apiError) {
	ids, lt, party := res.TableIDs, res.StartsAtLocal, res.PartySize
	if f.tableIDs != nil {
		if e := checkDistinct(f.tableIDs); e != nil {
			return false, placement{}, e
		}
		ids = f.tableIDs
	}
	if f.startsAt != nil {
		lt = *f.startsAt
	}
	if f.partySize != nil {
		party = *f.partySize
	}
	if sameTableSet(ids, res.TableIDs) && lt == res.StartsAtLocal && party == res.PartySize {
		return false, currentPlacement(res), nil
	}
	p, e := resolvePlacement(rest, ids, lt, party)
	return e == nil, p, e
}

// applyAmendment replaces the booking's values and accepted terms together,
// increments its revision once and records one changed entry.
func applyAmendment(res *Reservation, p placement, at time.Time) {
	oldIDs, oldLocal, oldParty := res.TableIDs, res.StartsAtLocal, res.PartySize
	res.TableIDs = p.tableIDs
	res.StartsAtLocal = p.local
	res.StartsAt = p.startsAt
	res.PartySize = p.partySize
	res.Terms = p.terms
	res.Revision++
	res.record(eventChanged, at, amendmentChanges(oldIDs, oldLocal, oldParty, res))
}

func (a *app) patchReservation(w http.ResponseWriter, r *http.Request, reference string) {
	body, e := readBody(w, r, maxBodyBytes)
	if e != nil {
		writeError(w, e)
		return
	}
	a.store.mu.Lock()
	defer a.store.mu.Unlock()
	st := a.store.state
	m, e := parseJSONObject(body)
	if e != nil {
		writeError(w, e)
		return
	}
	user, e := authenticate(st, r)
	if e != nil {
		writeError(w, e)
		return
	}
	res, rest, e := ownReservation(st, user, reference)
	if e != nil {
		writeError(w, e)
		return
	}
	if e := checkEditable(m, res, time.Now()); e != nil {
		writeError(w, e)
		return
	}
	f, e := parseBookingFields(m, false)
	if e != nil {
		writeError(w, e)
		return
	}
	changed, p, e := planAmendment(rest, res, f)
	if e != nil {
		writeError(w, e)
		return
	}
	if !changed {
		writeJSON(w, http.StatusOK, viewOf(res))
		return
	}
	// The booking's own occupancy never blocks its move.
	if st.setBusy(rest, p.tableIDs, p.startsAt, p.endsAt(), map[string]bool{res.ID: true}) {
		writeError(w, tableUnavailable)
		return
	}
	applyAmendment(res, p, stamp())
	rest.Revision++
	if sr := st.seriesByID[res.SeriesID]; sr != nil {
		sr.markException(res.ID)
		sr.Revision++
	}
	writeJSON(w, http.StatusOK, viewOf(res))
}
