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
	ReservationID string `json:"reservation_id"`
	Reference     string `json:"reference"`
	RestaurantID  string `json:"restaurant_id"`
	TableID       string `json:"table_id"`
	PartySize     int    `json:"party_size"`
	Status        string `json:"status"`
	StartsAtLocal string `json:"starts_at_local"`
	StartsAt      string `json:"starts_at"`
	EndsAt        string `json:"ends_at"`
	CreatedAt     string `json:"created_at"`
}

func viewOf(res *Reservation, rest *Restaurant) reservationView {
	return reservationView{
		ReservationID: res.ID,
		Reference:     res.Reference,
		RestaurantID:  res.RestaurantID,
		TableID:       res.TableID,
		PartySize:     res.PartySize,
		Status:        res.Status,
		StartsAtLocal: res.StartsAtLocal.String(),
		StartsAt:      formatTime(res.StartsAt),
		EndsAt:        formatTime(res.StartsAt.Add(rest.duration())),
		CreatedAt:     formatTime(res.CreatedAt.UTC()),
	}
}

// bookingFields are the amendable parts of a booking as sent by a client.
// A nil pointer means the field was absent.
type bookingFields struct {
	restaurantID *string
	tableID      *string
	startsAt     *localTime
	partySize    *int
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
	if s, present, ok := stringField(m, "table_id"); !ok {
		return f, malformed("table_id must be a string")
	} else if present {
		f.tableID = &s
	} else if requireAll {
		return f, invalid("table_id is required")
	}
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
// except table occupancy.
type placement struct {
	table     *Table
	local     localTime
	startsAt  time.Time
	partySize int
}

func (p placement) endsAt(rest *Restaurant) time.Time { return p.startsAt.Add(rest.duration()) }

// resolvePlacement applies the POST /reservations rules in order: table exists,
// the local time exists, it is inside opening hours and on the grid, the party fits.
func resolvePlacement(rest *Restaurant, tableID string, lt localTime, party int) (placement, *apiError) {
	tbl := rest.table(tableID)
	if tbl == nil {
		return placement{}, notFound("no such table at this restaurant")
	}
	start, ok := resolveLocal(rest.Location, lt)
	if !ok {
		return placement{}, fail(http.StatusUnprocessableEntity, codeInvalidLocalTime, "that local time does not exist on this date")
	}
	if e := rest.checkHours(lt); e != nil {
		return placement{}, e
	}
	if party > tbl.Capacity {
		return placement{}, fail(http.StatusUnprocessableEntity, codeExceedsCapacity, "party_size exceeds the table capacity")
	}
	return placement{table: tbl, local: lt, startsAt: start, partySize: party}, nil
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
// occupies the table during the half-open interval [start, end).
func (s *State) tableBusy(rest *Restaurant, tableID string, start, end time.Time, exclude map[string]bool) bool {
	for _, res := range s.reservations {
		if res.Status != statusConfirmed || res.RestaurantID != rest.ID || res.TableID != tableID || exclude[res.ID] {
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
	p, e := resolvePlacement(rest, *f.tableID, *f.startsAt, *f.partySize)
	if e != nil {
		writeError(w, e)
		return
	}
	if st.tableBusy(rest, p.table.ID, p.startsAt, p.endsAt(rest), nil) {
		writeError(w, fail(http.StatusConflict, codeTableUnavailable, "the table is taken for an overlapping interval"))
		return
	}
	res := &Reservation{
		ID:            st.newReservationID(),
		Reference:     st.newReference(),
		UserID:        user.ID,
		RestaurantID:  rest.ID,
		TableID:       p.table.ID,
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
	tableID, lt, party := res.TableID, res.StartsAtLocal, res.PartySize
	if f.tableID != nil {
		tableID = *f.tableID
	}
	if f.startsAt != nil {
		lt = *f.startsAt
	}
	if f.partySize != nil {
		party = *f.partySize
	}
	return resolvePlacement(rest, tableID, lt, party)
}

func apply(res *Reservation, p placement) {
	res.TableID = p.table.ID
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
	if st.tableBusy(rest, p.table.ID, p.startsAt, p.endsAt(rest), map[string]bool{res.ID: true}) {
		writeError(w, fail(http.StatusConflict, codeTableUnavailable, "the table is taken for an overlapping interval"))
		return
	}
	apply(res, p)
	writeJSON(w, http.StatusOK, viewOf(res, rest))
}
