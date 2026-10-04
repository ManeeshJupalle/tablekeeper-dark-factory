package main

import (
	"encoding/json"
	"net/http"
	"time"
)

const (
	minMoves  = 1
	maxMoves  = 8
	pathMoves = "/reservation-moves"
)

type moveItem struct {
	reference string
	fields    map[string]any
}

// parseMoves validates the batch shape (§11): 1..8 objects with distinct string
// references. Anything else is 422 validation_failed.
func parseMoves(m map[string]any) ([]moveItem, *apiError) {
	raw, present := m["moves"]
	if !present {
		return nil, invalid("moves is required")
	}
	arr, ok := raw.([]any)
	if !ok {
		return nil, invalid("moves must be an array")
	}
	if len(arr) < minMoves || len(arr) > maxMoves {
		return nil, invalid("moves must contain 1 to 8 items")
	}
	items := make([]moveItem, 0, len(arr))
	seen := map[string]bool{}
	for _, v := range arr {
		obj, ok := v.(map[string]any)
		if !ok {
			return nil, invalid("each move must be an object")
		}
		ref, ok := obj["reference"].(string)
		if !ok {
			return nil, invalid("each move needs a string reference")
		}
		if seen[ref] {
			return nil, invalid("references in a batch must be distinct")
		}
		seen[ref] = true
		items = append(items, moveItem{reference: ref, fields: obj})
	}
	return items, nil
}

type plannedMove struct {
	res     *Reservation
	p       placement
	changed bool
}

func overlaps(aStart, aEnd, bStart, bEnd time.Time) bool {
	return aStart.Before(bEnd) && bStart.Before(aEnd)
}

func (a *app) moveReservations(w http.ResponseWriter, r *http.Request) {
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
	k := idemKey{user.ID, http.MethodPost, pathMoves, key}
	canonical := canonicalJSON(m)
	if replay(w, st, k, canonical) {
		return
	}

	items, e := parseMoves(m)
	if e != nil {
		writeError(w, e)
		return
	}

	// Non-occupancy errors in input order. For one booking: expected revision,
	// cancelled, the old accepted cutoff, then the fields under the resulting
	// date's policy (individual PATCH semantics).
	checkedAt := time.Now()
	var rest *Restaurant
	planned := make([]plannedMove, 0, len(items))
	for _, it := range items {
		res, own, e := ownReservation(st, user, it.reference)
		if e != nil {
			writeError(w, e)
			return
		}
		if rest == nil {
			rest = own
		} else if own != rest {
			writeError(w, invalid("all moves must belong to the same restaurant"))
			return
		}
		if e := checkEditable(it.fields, res, checkedAt); e != nil {
			writeError(w, e)
			return
		}
		f, e := parseBookingFields(it.fields, false)
		if e != nil {
			writeError(w, e)
			return
		}
		changed, p, e := planAmendment(rest, res, f)
		if e != nil {
			writeError(w, e)
			return
		}
		planned = append(planned, plannedMove{res, p, changed})
	}

	// Occupancy: the resulting bookings must not overlap each other or any
	// confirmed booking outside the batch. Unchanged listed bookings keep their
	// own slots and accepted durations.
	listed := map[string]bool{}
	for _, pm := range planned {
		listed[pm.res.ID] = true
	}
	for i, pm := range planned {
		start, end := pm.p.startsAt, pm.p.endsAt()
		if st.setBusy(rest, pm.p.tableIDs, start, end, listed) {
			writeError(w, tableUnavailable)
			return
		}
		for _, other := range planned[:i] {
			if sharesTable(other.p.tableIDs, pm.p.tableIDs) && overlaps(start, end, other.p.startsAt, other.p.endsAt()) {
				writeError(w, tableUnavailable)
				return
			}
		}
	}

	// Commit everything together: one revision and one history entry per changed
	// booking, the restaurant and each affected series once per batch.
	at := stamp()
	touched := map[*Series]bool{}
	anyChanged := false
	views := make([]reservationView, 0, len(planned))
	for _, pm := range planned {
		if pm.changed {
			applyAmendment(pm.res, pm.p, at)
			anyChanged = true
			if sr := st.seriesByID[pm.res.SeriesID]; sr != nil {
				sr.markException(pm.res.ID)
				touched[sr] = true
			}
		}
		views = append(views, viewOf(pm.res))
	}
	if anyChanged {
		rest.Revision++
	}
	for sr := range touched {
		sr.Revision++
	}
	resp, _ := json.Marshal(map[string]any{"reservations": views})
	st.idem[k] = &IdemRecord{idemKey: k, Body: canonical, Status: http.StatusCreated, Response: resp}
	writeRaw(w, http.StatusCreated, resp)
}
