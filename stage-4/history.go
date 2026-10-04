package main

import (
	"net/http"
	"time"
)

// Reservation history and decision (stage 3). Every state-changing operation on
// a booking appends exactly one entry; replays, no-op amendments and failures
// append nothing. Entries are never edited afterwards.

const (
	eventCreated   = "created"
	eventChanged   = "changed"
	eventCancelled = "cancelled"
	// eventReassigned records a seating repair applied by a plan (stage 4).
	eventReassigned = "reassigned"

	fieldTableID       = "table_id"
	fieldTableIDs      = "table_ids"
	fieldStartsAtLocal = "starts_at_local"
	fieldPartySize     = "party_size"
)

// Change is one field of a history entry. From and To hold a string, an int, a
// list of table ids or nil; imported values keep their decoded JSON form.
type Change struct {
	Field string `json:"field"`
	From  any    `json:"from"`
	To    any    `json:"to"`
}

type HistoryEntry struct {
	Seq      int
	At       time.Time
	Event    string
	Changes  []Change
	Revision int    // the reservation's revision after this event
	Terms    *Terms // the terms accepted at this event
	PlanID   string // set on reassigned entries
}

// tableChange describes a table-set change: table_id between two single tables,
// table_ids (complete lists, declared order) whenever a pair is involved.
func tableChange(from, to []string) Change {
	if len(to) == 1 && (from == nil || len(from) == 1) {
		c := Change{Field: fieldTableID, To: to[0]}
		if from != nil {
			c.From = from[0]
		}
		return c
	}
	c := Change{Field: fieldTableIDs, To: append([]string{}, to...)}
	if from != nil {
		c.From = append([]string{}, from...)
	}
	return c
}

func creationChanges(res *Reservation) []Change {
	return []Change{
		tableChange(nil, res.TableIDs),
		{Field: fieldStartsAtLocal, To: res.StartsAtLocal.String()},
		{Field: fieldPartySize, To: res.PartySize},
	}
}

// amendmentChanges lists only the fields that differ, in the order table set,
// starts_at_local, party_size.
func amendmentChanges(oldIDs []string, oldLocal localTime, oldParty int, res *Reservation) []Change {
	changes := []Change{}
	if !sameTableSet(oldIDs, res.TableIDs) {
		changes = append(changes, tableChange(oldIDs, res.TableIDs))
	}
	if oldLocal != res.StartsAtLocal {
		changes = append(changes, Change{Field: fieldStartsAtLocal, From: oldLocal.String(), To: res.StartsAtLocal.String()})
	}
	if oldParty != res.PartySize {
		changes = append(changes, Change{Field: fieldPartySize, From: oldParty, To: res.PartySize})
	}
	return changes
}

// record appends an entry carrying the reservation's current revision and terms.
func (res *Reservation) record(event string, at time.Time, changes []Change) *HistoryEntry {
	h := &HistoryEntry{
		Seq:      len(res.History) + 1,
		At:       at,
		Event:    event,
		Changes:  changes,
		Revision: res.Revision,
		Terms:    res.Terms,
	}
	res.History = append(res.History, h)
	return h
}

// reassignmentChange always names the complete table sets, even single to single.
func reassignmentChange(from, to []string) Change {
	return Change{Field: fieldTableIDs, From: append([]string{}, from...), To: append([]string{}, to...)}
}

type historyEntryView struct {
	Seq           int       `json:"seq"`
	At            string    `json:"at"`
	Event         string    `json:"event"`
	Changes       []Change  `json:"changes"`
	Revision      int       `json:"revision"`
	AcceptedTerms termsView `json:"accepted_terms"`
	PlanID        string    `json:"plan_id,omitempty"`
}

func historyViewOf(res *Reservation) []historyEntryView {
	out := make([]historyEntryView, 0, len(res.History))
	for _, h := range res.History {
		changes := h.Changes
		if changes == nil {
			changes = []Change{}
		}
		out = append(out, historyEntryView{
			Seq: h.Seq, At: formatTime(h.At.UTC()), Event: h.Event, Changes: changes,
			Revision: h.Revision, AcceptedTerms: termsViewOf(h.Terms), PlanID: h.PlanID,
		})
	}
	return out
}

// ownerOnly resolves a reservation for history and decision: anyone but the
// owner, signed in or not, gets the same 404 as for someone else's booking.
func ownerOnly(st *State, r *http.Request, reference string) (*Reservation, *apiError) {
	user, e := authenticate(st, r)
	if e != nil {
		return nil, notFound("no such reservation")
	}
	res, _, e := ownReservation(st, user, reference)
	return res, e
}

func (a *app) reservationHistory(w http.ResponseWriter, r *http.Request, reference string) {
	a.store.mu.Lock()
	defer a.store.mu.Unlock()
	res, e := ownerOnly(a.store.state, r, reference)
	if e != nil {
		writeError(w, e)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"reference": res.Reference, "entries": historyViewOf(res)})
}

func (a *app) reservationDecision(w http.ResponseWriter, r *http.Request, reference string) {
	a.store.mu.Lock()
	defer a.store.mu.Unlock()
	res, e := ownerOnly(a.store.state, r, reference)
	if e != nil {
		writeError(w, e)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"reference": res.Reference, "revision": res.Revision, "accepted_terms": termsViewOf(res.Terms),
	})
}
