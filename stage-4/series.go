package main

import (
	"encoding/json"
	"net/http"
	"regexp"
	"time"
)

// Recurring reservations (stage 3). A series adopts an existing booking as
// occurrence 0 and books the remaining occurrences at a weekly interval, all in
// one atomic step: either every occurrence is created or nothing changes.

const (
	pathSeries       = "/series"
	minSeriesCount   = 2
	maxSeriesCount   = 12
	minSeriesWeeks   = 1
	maxSeriesWeeks   = 4
	daysPerWeek      = 7
	anchorOccurrence = 0
)

type Occurrence struct {
	Index         int
	ReservationID string
	Exception     bool // set permanently by a real individual amendment
}

type Series struct {
	ID            string
	UserID        string
	RestaurantID  string
	Revision      int
	IntervalWeeks int
	Occurrences   []*Occurrence
}

// markException flags the occurrence held by a reservation as a diner exception.
func (s *Series) markException(reservationID string) {
	for _, o := range s.Occurrences {
		if o.ReservationID == reservationID {
			o.Exception = true
		}
	}
}

type occurrenceView struct {
	Index       int             `json:"index"`
	Reference   string          `json:"reference"`
	Exception   bool            `json:"exception"`
	Reservation reservationView `json:"reservation"`
}

type seriesView struct {
	SeriesID      string           `json:"series_id"`
	Revision      int              `json:"revision"`
	IntervalWeeks int              `json:"interval_weeks"`
	Occurrences   []occurrenceView `json:"occurrences"`
}

func seriesViewOf(st *State, s *Series) seriesView {
	v := seriesView{SeriesID: s.ID, Revision: s.Revision, IntervalWeeks: s.IntervalWeeks, Occurrences: []occurrenceView{}}
	for _, o := range s.Occurrences {
		res := st.reservationByID[o.ReservationID]
		v.Occurrences = append(v.Occurrences, occurrenceView{
			Index: o.Index, Reference: res.Reference, Exception: o.Exception, Reservation: viewOf(res),
		})
	}
	return v
}

// seriesInt reads count or interval_weeks: any missing, mistyped (booleans and
// non-integers included) or out-of-range value is 422.
func seriesInt(m map[string]any, key string, min, max int64) (int, *apiError) {
	n, ok := jsonInt(m[key])
	if !ok || n < min || n > max {
		return 0, invalid(key + " must be an integer in the stated range")
	}
	return int(n), nil
}

func (a *app) createSeries(w http.ResponseWriter, r *http.Request) {
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
	k := idemKey{user.ID, http.MethodPost, pathSeries, key}
	canonical := canonicalJSON(m)
	if replay(w, st, k, canonical) {
		return
	}

	anchorRef, present, ok := stringField(m, "anchor_reference")
	if !ok {
		writeError(w, malformed("anchor_reference must be a string"))
		return
	}
	if !present {
		writeError(w, invalid("anchor_reference is required"))
		return
	}
	count, e := seriesInt(m, "count", minSeriesCount, maxSeriesCount)
	if e != nil {
		writeError(w, e)
		return
	}
	weeks, e := seriesInt(m, "interval_weeks", minSeriesWeeks, maxSeriesWeeks)
	if e != nil {
		writeError(w, e)
		return
	}
	anchor, rest, e := ownReservation(st, user, anchorRef)
	if e != nil {
		writeError(w, e)
		return
	}
	if anchor.Status == statusCancelled {
		writeError(w, fail(http.StatusConflict, codeCancelled, "a cancelled reservation cannot anchor a series"))
		return
	}
	if anchor.SeriesID != "" {
		writeError(w, fail(http.StatusConflict, codeAlreadyInSeries, "the reservation already belongs to a series"))
		return
	}
	if cutoffPassed(anchor, time.Now()) {
		writeError(w, fail(http.StatusConflict, codeCutoffPassed, "the anchor's cutoff has passed"))
		return
	}

	// Plan every generated occurrence first; the first failure in index order
	// decides the error and nothing is written.
	plans := make([]placement, 0, count-1)
	for i := 1; i < count; i++ {
		lt := localTime{anchor.StartsAtLocal.localDate.addDays(i * weeks * daysPerWeek), anchor.StartsAtLocal.Minute}
		p, e := resolvePlacement(rest, anchor.TableIDs, lt, anchor.PartySize)
		if e != nil {
			writeError(w, e)
			return
		}
		busy := st.setBusy(rest, p.tableIDs, p.startsAt, p.endsAt(), nil)
		for _, q := range plans {
			busy = busy || (sharesTable(q.tableIDs, p.tableIDs) && overlaps(p.startsAt, p.endsAt(), q.startsAt, q.endsAt()))
		}
		if busy {
			writeError(w, tableUnavailable)
			return
		}
		plans = append(plans, p)
	}

	at := stamp()
	sr := &Series{ID: st.newSeriesID(), UserID: user.ID, RestaurantID: rest.ID, Revision: initialRevision, IntervalWeeks: weeks}
	sr.Occurrences = append(sr.Occurrences, &Occurrence{Index: anchorOccurrence, ReservationID: anchor.ID})
	anchor.SeriesID = sr.ID
	for i, p := range plans {
		res := st.newReservation(user, rest, p, at)
		res.SeriesID = sr.ID
		sr.Occurrences = append(sr.Occurrences, &Occurrence{Index: i + 1, ReservationID: res.ID})
	}
	st.addSeries(sr)
	rest.Revision++
	resp, _ := json.Marshal(seriesViewOf(st, sr))
	st.idem[k] = &IdemRecord{idemKey: k, Body: canonical, Status: http.StatusCreated, Response: resp}
	writeRaw(w, http.StatusCreated, resp)
}

// getSeries is owner-only: another user or no token gets 404.
func (a *app) getSeries(w http.ResponseWriter, r *http.Request, id string) {
	a.store.mu.Lock()
	defer a.store.mu.Unlock()
	st := a.store.state
	user, e := authenticate(st, r)
	sr := st.seriesByID[id]
	if e != nil || sr == nil || sr.UserID != user.ID {
		writeError(w, notFound("no such series"))
		return
	}
	writeJSON(w, http.StatusOK, seriesViewOf(st, sr))
}

// Amending a recurring series (stage 4): move the clock time of every eligible
// occurrence at or after from_index, on its own scheduled date, in one step.

var clockTimeRE = regexp.MustCompile(`^([01][0-9]|2[0-3]):([0-5][0-9])$`)

func seriesAmendPath(id string) string { return pathSeries + "/" + id + "/amend" }

type plannedOccurrence struct {
	res *Reservation
	p   placement
}

func (a *app) amendSeries(w http.ResponseWriter, r *http.Request, id string) {
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
	sr := st.seriesByID[id]
	if sr == nil || sr.UserID != user.ID {
		writeError(w, notFound("no such series"))
		return
	}
	key, e := idempotencyKey(r)
	if e != nil {
		writeError(w, e)
		return
	}
	k := idemKey{user.ID, http.MethodPost, seriesAmendPath(sr.ID), key}
	canonical := canonicalJSON(m)
	if replay(w, st, k, canonical) {
		return
	}

	// Every input problem, wrong JSON types included, is 422.
	expected, ok := jsonInt(m["expected_revision"])
	if !ok || expected < 1 {
		writeError(w, invalid("expected_revision must be a positive integer"))
		return
	}
	fromIndex, ok := jsonInt(m["from_index"])
	if !ok || fromIndex < 0 || fromIndex >= int64(len(sr.Occurrences)) {
		writeError(w, invalid("from_index must be an integer in 0..count-1"))
		return
	}
	clock, _ := m["local_time"].(string)
	match := clockTimeRE.FindStringSubmatch(clock)
	if match == nil {
		writeError(w, invalid("local_time must be HH:MM between 00:00 and 23:59"))
		return
	}
	minute := atoi(match[1])*minutesPerHour + atoi(match[2])
	if expected != int64(sr.Revision) {
		writeError(w, fail(http.StatusConflict, codeStaleRevision, "the series has changed since that revision"))
		return
	}
	rest := st.restaurantsByID[sr.RestaurantID]

	// Non-occupancy checks in occurrence-index order: the old accepted cutoff,
	// then the resulting date's policy, exactly like an individual PATCH.
	checkedAt := time.Now()
	planned := []plannedOccurrence{}
	for _, o := range sr.Occurrences[fromIndex:] {
		res := st.reservationByID[o.ReservationID]
		if o.Exception || res.Status == statusCancelled {
			continue
		}
		lt := localTime{res.StartsAtLocal.localDate, minute}
		if lt == res.StartsAtLocal {
			continue // no-op: keeps its terms and revision
		}
		if cutoffPassed(res, checkedAt) {
			writeError(w, fail(http.StatusConflict, codeCutoffPassed, "an occurrence is past its amendment cutoff"))
			return
		}
		p, e := resolvePlacement(rest, res.TableIDs, lt, res.PartySize)
		if e != nil {
			writeError(w, e)
			return
		}
		planned = append(planned, plannedOccurrence{res, p})
	}

	// Occupancy: the changed occurrences release their old slots together and
	// must clear unchanged occurrences, other bookings, closures and each other.
	moving := map[string]bool{}
	for _, po := range planned {
		moving[po.res.ID] = true
	}
	for i, po := range planned {
		start, end := po.p.startsAt, po.p.endsAt()
		busy := st.setBusy(rest, po.p.tableIDs, start, end, moving)
		for _, other := range planned[:i] {
			busy = busy || (sharesTable(other.p.tableIDs, po.p.tableIDs) && overlaps(start, end, other.p.startsAt, other.p.endsAt()))
		}
		if busy {
			writeError(w, tableUnavailable)
			return
		}
	}

	if len(planned) > 0 {
		at := stamp()
		for _, po := range planned {
			applyAmendment(po.res, po.p, at) // ordinary changed entry; no exception
		}
		sr.Revision++
		rest.Revision++
	}
	resp, _ := json.Marshal(seriesViewOf(st, sr))
	st.idem[k] = &IdemRecord{idemKey: k, Body: canonical, Status: http.StatusCreated, Response: resp}
	writeRaw(w, http.StatusCreated, resp)
}
