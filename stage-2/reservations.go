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
	ReservationID string   `json:"reservation_id"`
	Reference     string   `json:"reference"`
	RestaurantID  string   `json:"restaurant_id"`
	TableIDs      []string `json:"table_ids"`
	TableID       *string  `json:"table_id,omitempty"` // only for a single-table set
	PartySize     int      `json:"party_size"`
	Status        string   `json:"status"`
	StartsAtLocal string   `json:"starts_at_local"`
	StartsAt      string   `json:"starts_at"`
	EndsAt        string   `json:"ends_at"`
	CreatedAt     string   `json:"created_at"`
}

func viewOf(res *Reservation, rest *Restaurant) reservationView {
	v := reservationView{
		ReservationID: res.ID,
		Reference:     res.Reference,
		RestaurantID:  res.RestaurantID,
		TableIDs:      append([]string{}, res.TableIDs...),
		PartySize:     res.PartySize,
		Status:        res.Status,
		StartsAtLocal: res.StartsAtLocal.String(),
		StartsAt:      formatTime(res.StartsAt),
		EndsAt:        formatTime(res.StartsAt.Add(rest.duration())),
		CreatedAt:     formatTime(res.CreatedAt.UTC()),
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
// except table occupancy. tableIDs is in canonical (fixture/combinable) order.
type placement struct {
	tableIDs  []string
	local     localTime
	startsAt  time.Time
	partySize int
}

func (p placement) endsAt(rest *Restaurant) time.Time { return p.startsAt.Add(rest.duration()) }

// resolveTableSet validates a requested table set against the restaurant: no
// duplicates, at most a pair, every table known, and a pair declared combinable.
// It returns the set in canonical order and its summed capacity.
func resolveTableSet(rest *Restaurant, ids []string) ([]string, int, *apiError) {
	seen := map[string]bool{}
	for _, id := range ids {
		if seen[id] {
			return nil, 0, invalid("duplicate table id in the set")
		}
		seen[id] = true
	}
	if len(ids) > maxTablesPerBooking {
		return nil, 0, fail(http.StatusUnprocessableEntity, codeCombinationNotAllowed, "at most two tables may be combined")
	}
	capacity := 0
	for _, id := range ids {
		t := rest.table(id)
		if t == nil {
			return nil, 0, notFound("no such table at this restaurant")
		}
		capacity += t.Capacity
	}
	if len(ids) == 1 {
		return []string{ids[0]}, capacity, nil
	}
	p := rest.pair(ids[0], ids[1])
	if p == nil {
		return nil, 0, fail(http.StatusUnprocessableEntity, codeCombinationNotAllowed, "those tables are not declared combinable")
	}
	return []string{p[0], p[1]}, capacity, nil
}

// resolvePlacement applies the POST /reservations rules in order: the table set
// is valid, the local time exists, it is inside opening hours and on the grid,
// and the party fits the (summed) capacity.
func resolvePlacement(rest *Restaurant, ids []string, lt localTime, party int) (placement, *apiError) {
	set, capacity, e := resolveTableSet(rest, ids)
	if e != nil {
		return placement{}, e
	}
	start, ok := resolveLocal(rest.Location, lt)
	if !ok {
		return placement{}, fail(http.StatusUnprocessableEntity, codeInvalidLocalTime, "that local time does not exist on this date")
	}
	if e := rest.checkHours(lt); e != nil {
		return placement{}, e
	}
	if party > capacity {
		return placement{}, fail(http.StatusUnprocessableEntity, codeExceedsCapacity, "party_size exceeds the seating capacity")
	}
	return placement{tableIDs: set, local: lt, startsAt: start, partySize: party}, nil
}

// checkHours finds the opening window containing the start and applies the slot
// grid and closing rules; a start outside every window is outside opening hours.
func (r *Restaurant) checkHours(lt localTime) *apiError {
	weekday := weekdayOf(lt.localDate)
	for _, h := range r.Hours {
		if h.Weekday != weekday || lt.Minute < h.opensMin || lt.Minute >= h.closesMin {
			continue
		}
		if (lt.Minute-h.opensMin)%r.SlotMinutes != 0 {
			return fail(http.StatusUnprocessableEntity, codeNotOnGrid, "starts_at_local is not on the slot grid")
		}
		if lt.Minute+r.DurationMinutes > h.closesMin {
			return fail(http.StatusUnprocessableEntity, codeOutsideHours, "the reservation would end after closing time")
		}
		return nil
	}
	return fail(http.StatusUnprocessableEntity, codeOutsideHours, "the restaurant is closed at that time")
}

// tableBusy reports whether a confirmed reservation other than those in exclude
// occupies the table during the half-open interval [start, end). A combination
// booking occupies every one of its tables.
func (s *State) tableBusy(rest *Restaurant, tableID string, start, end time.Time, exclude map[string]bool) bool {
	return s.setBusy(rest, []string{tableID}, start, end, exclude)
}

// setBusy reports whether any table of the set is occupied during [start, end).
func (s *State) setBusy(rest *Restaurant, ids []string, start, end time.Time, exclude map[string]bool) bool {
	for _, res := range s.reservations {
		if res.Status != statusConfirmed || res.RestaurantID != rest.ID || exclude[res.ID] || !sharesTable(res.TableIDs, ids) {
			continue
		}
		resEnd := res.StartsAt.Add(rest.duration())
		if res.StartsAt.Before(end) && start.Before(resEnd) {
			return true
		}
	}
	return false
}

func cutoffPassed(rest *Restaurant, res *Reservation, now time.Time) bool {
	return !now.Before(res.StartsAt.Add(-rest.cutoff()))
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
	if st.setBusy(rest, p.tableIDs, p.startsAt, p.endsAt(rest), nil) {
		writeError(w, tableUnavailable)
		return
	}
	res := &Reservation{
		ID:            st.newReservationID(),
		Reference:     st.newReference(),
		UserID:        user.ID,
		RestaurantID:  rest.ID,
		TableIDs:      p.tableIDs,
		PartySize:     p.partySize,
		Status:        statusConfirmed,
		StartsAtLocal: p.local,
		StartsAt:      p.startsAt,
		CreatedAt:     time.Now().UTC().Truncate(time.Second),
	}
	st.addReservation(res)
	resp, _ := json.Marshal(viewOf(res, rest))
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
		out = append(out, viewOf(res, st.restaurantsByID[res.RestaurantID]))
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
	res, rest, e := ownReservation(st, user, reference)
	if e != nil {
		writeError(w, e)
		return
	}
	writeJSON(w, http.StatusOK, viewOf(res, rest))
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
		writeJSON(w, http.StatusOK, viewOf(res, rest))
		return
	}
	if cutoffPassed(rest, res, time.Now()) {
		writeError(w, fail(http.StatusConflict, codeCutoffPassed, "the cancellation cutoff has passed"))
		return
	}
	res.Status = statusCancelled
	writeJSON(w, http.StatusOK, viewOf(res, rest))
}

// amend resolves a change to an existing booking against the ordinary rules,
// without touching occupancy; the caller decides how occupancy is checked.
func amend(rest *Restaurant, res *Reservation, f bookingFields) (placement, *apiError) {
	ids, lt, party := res.TableIDs, res.StartsAtLocal, res.PartySize
	if f.tableIDs != nil {
		ids = f.tableIDs
	}
	if f.startsAt != nil {
		lt = *f.startsAt
	}
	if f.partySize != nil {
		party = *f.partySize
	}
	return resolvePlacement(rest, ids, lt, party)
}

func apply(res *Reservation, p placement) {
	res.TableIDs = p.tableIDs
	res.StartsAtLocal = p.local
	res.StartsAt = p.startsAt
	res.PartySize = p.partySize
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
	if res.Status == statusCancelled {
		writeError(w, fail(http.StatusConflict, codeCancelled, "a cancelled reservation cannot be changed"))
		return
	}
	if cutoffPassed(rest, res, time.Now()) {
		writeError(w, fail(http.StatusConflict, codeCutoffPassed, "the amendment cutoff has passed"))
		return
	}
	f, e := parseBookingFields(m, false)
	if e != nil {
		writeError(w, e)
		return
	}
	p, e := amend(rest, res, f)
	if e != nil {
		writeError(w, e)
		return
	}
	// The booking's own occupancy never blocks its move.
	if st.setBusy(rest, p.tableIDs, p.startsAt, p.endsAt(rest), map[string]bool{res.ID: true}) {
		writeError(w, tableUnavailable)
		return
	}
	apply(res, p)
	writeJSON(w, http.StatusOK, viewOf(res, rest))
}
