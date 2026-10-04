package main

import (
	"encoding/json"
	"net/http"
	"sort"
	"time"
)

// Seating changes after a table closure (stage 4). A manager previews a plan,
// which is stored and changes nothing else, then applies it: the closure and
// every reassignment are recorded together under the store lock.

// Planning bounds: within them a plan is always computed exactly.
const (
	maxPlanningTables   = 6
	maxPlanningPairs    = 4
	maxPlanningBookings = 6
)

const (
	codePlanningLimit      = "planning_limit"
	codeNoFeasiblePlan     = "no_feasible_plan"
	codeStalePlan          = "stale_plan"
	codePlanAlreadyApplied = "plan_already_applied"
)

// Closure takes one table out of service for the half-open interval [From, To).
// The original RFC 3339 strings are kept so responses echo what was supplied.
type Closure struct {
	TableID string
	From    time.Time
	To      time.Time
	FromRaw string
	ToRaw   string
	PlanID  string
}

func (c Closure) overlaps(start, end time.Time) bool { return overlaps(c.From, c.To, start, end) }

// closed reports whether an applied closure covers any table of the set during [start, end).
func (r *Restaurant) closed(ids []string, start, end time.Time) bool {
	for _, c := range r.Closures {
		if c.overlaps(start, end) && sharesTable([]string{c.TableID}, ids) {
			return true
		}
	}
	return false
}

type Assignment struct {
	ReservationID string
	Reference     string
	TableIDs      []string
	Changed       bool
}

// Plan is a stored preview. RestaurantRevision is the revision it was computed
// against; any later state change at the restaurant makes it stale.
type Plan struct {
	ID                 string
	RestaurantID       string
	RestaurantRevision int
	Closure            Closure
	Assignments        []Assignment // ascending reference order
	MovedCount         int
	UnusedSeats        int
	Applied            bool
}

type closureView struct {
	TableID string `json:"table_id"`
	From    string `json:"from"`
	To      string `json:"to"`
}

type assignmentView struct {
	Reference string   `json:"reference"`
	TableIDs  []string `json:"table_ids"`
	Changed   bool     `json:"changed"`
}

type planView struct {
	PlanID             string           `json:"plan_id"`
	RestaurantRevision int              `json:"restaurant_revision"`
	Closure            closureView      `json:"closure"`
	Assignments        []assignmentView `json:"assignments"`
	MovedCount         int              `json:"moved_count"`
	UnusedSeats        int              `json:"unused_seats"`
}

func planViewOf(p *Plan) planView {
	v := planView{
		PlanID: p.ID, RestaurantRevision: p.RestaurantRevision,
		Closure:     closureView{p.Closure.TableID, p.Closure.FromRaw, p.Closure.ToRaw},
		Assignments: []assignmentView{}, MovedCount: p.MovedCount, UnusedSeats: p.UnusedSeats,
	}
	for _, a := range p.Assignments {
		v.Assignments = append(v.Assignments, assignmentView{a.Reference, append([]string{}, a.TableIDs...), a.Changed})
	}
	return v
}

// seatingOptions lists every single in fixture order, then every declared pair
// in combinable order; an option's index is its rank.
func seatingOptions(rest *Restaurant) [][]string {
	out := make([][]string, 0, len(rest.Tables)+len(rest.Combinable))
	for _, t := range rest.Tables {
		out = append(out, []string{t.ID})
	}
	for _, p := range rest.Combinable {
		out = append(out, []string{p[0], p[1]})
	}
	return out
}

// candidate is one feasible option for one considered booking.
type candidate struct {
	option  []string
	changed bool
	unused  int
}

// solvePlan finds the feasible assignment minimising, lexicographically, the
// number of bookings whose table set changes, the total unused seats, and the
// vector of option ranks in reference order. Search is exhaustive with pruning;
// options are tried in rank order, so among equal (moved, unused) totals the
// first complete assignment found has the smallest rank vector.
func solvePlan(considered []*Reservation, cands [][]candidate) ([]candidate, bool) {
	n := len(considered)
	chosen := make([]candidate, n)
	var best []candidate
	bestMoved, bestUnused := 0, 0
	var search func(i, moved, unused int)
	search = func(i, moved, unused int) {
		if best != nil && (moved > bestMoved || (moved == bestMoved && unused >= bestUnused)) {
			return // cannot beat the best found so far (ties lose on rank order)
		}
		if i == n {
			best = append([]candidate{}, chosen...)
			bestMoved, bestUnused = moved, unused
			return
		}
		b := considered[i]
		for _, c := range cands[i] {
			clash := false
			for j := 0; j < i && !clash; j++ {
				o := considered[j]
				clash = sharesTable(chosen[j].option, c.option) && overlaps(b.StartsAt, b.endsAt(), o.StartsAt, o.endsAt())
			}
			if clash {
				continue
			}
			chosen[i] = c
			m := moved
			if c.changed {
				m++
			}
			search(i+1, m, unused+c.unused)
		}
	}
	search(0, 0, 0)
	return best, best != nil
}

// parseInstant reads an RFC 3339 instant with an explicit offset.
func parseInstant(m map[string]any, key string) (time.Time, string, *apiError) {
	raw, present, ok := stringField(m, key)
	if !ok {
		return time.Time{}, "", malformed(key + " must be a string")
	}
	if !present {
		return time.Time{}, "", invalid(key + " is required")
	}
	t, err := time.Parse(time.RFC3339, raw)
	if err != nil {
		return time.Time{}, "", invalid(key + " must be an RFC 3339 instant with an explicit offset")
	}
	return t, raw, nil
}

func replansPath(restaurantID string) string { return "/restaurants/" + restaurantID + "/replans" }

// managerWrite resolves the shared prefix of a manager's idempotent write:
// body, caller, restaurant, permission, key and replay. handled is true when a
// response was already written.
func managerWrite(w http.ResponseWriter, r *http.Request, st *State, body []byte, restaurantID, path string) (m map[string]any, rest *Restaurant, k idemKey, canonical string, handled bool) {
	m, e := parseJSONObject(body)
	if e != nil {
		writeError(w, e)
		return nil, nil, k, "", true
	}
	user, e := authenticate(st, r)
	if e != nil {
		writeError(w, e)
		return nil, nil, k, "", true
	}
	rest = st.restaurantsByID[restaurantID]
	if rest == nil {
		writeError(w, notFound("no such restaurant"))
		return nil, nil, k, "", true
	}
	if !rest.isManager(user.ID) {
		writeError(w, fail(http.StatusForbidden, codeForbidden, "only the restaurant's managers may do this"))
		return nil, nil, k, "", true
	}
	key, e := idempotencyKey(r)
	if e != nil {
		writeError(w, e)
		return nil, nil, k, "", true
	}
	k = idemKey{user.ID, http.MethodPost, path, key}
	canonical = canonicalJSON(m)
	if replay(w, st, k, canonical) {
		return nil, nil, k, "", true
	}
	return m, rest, k, canonical, false
}

func (a *app) previewReplan(w http.ResponseWriter, r *http.Request, restaurantID string) {
	body, e := readBody(w, r, maxBodyBytes)
	if e != nil {
		writeError(w, e)
		return
	}
	a.store.mu.Lock()
	defer a.store.mu.Unlock()
	st := a.store.state
	m, rest, k, canonical, handled := managerWrite(w, r, st, body, restaurantID, replansPath(restaurantID))
	if handled {
		return
	}

	tableID, present, ok := stringField(m, "table_id")
	if !ok {
		writeError(w, malformed("table_id must be a string"))
		return
	}
	if !present {
		writeError(w, invalid("table_id is required"))
		return
	}
	from, fromRaw, e := parseInstant(m, "from")
	if e != nil {
		writeError(w, e)
		return
	}
	to, toRaw, e := parseInstant(m, "to")
	if e != nil {
		writeError(w, e)
		return
	}
	if !from.Before(to) {
		writeError(w, invalid("from must be earlier than to"))
		return
	}
	if rest.table(tableID) == nil {
		writeError(w, notFound("no such table at this restaurant"))
		return
	}
	if len(rest.Tables) > maxPlanningTables || len(rest.Combinable) > maxPlanningPairs {
		writeError(w, fail(http.StatusUnprocessableEntity, codePlanningLimit, "the restaurant is larger than the planning bounds"))
		return
	}
	closure := Closure{TableID: tableID, From: from, To: to, FromRaw: fromRaw, ToRaw: toRaw}

	// Considered: every confirmed booking here overlapping the closure, on any table.
	considered := []*Reservation{}
	inPlan := map[string]bool{}
	for _, res := range st.reservations {
		if res.Status == statusConfirmed && res.RestaurantID == rest.ID && closure.overlaps(res.StartsAt, res.endsAt()) {
			considered = append(considered, res)
			inPlan[res.ID] = true
		}
	}
	if len(considered) > maxPlanningBookings {
		writeError(w, fail(http.StatusUnprocessableEntity, codePlanningLimit, "too many bookings overlap the closure"))
		return
	}
	sort.Slice(considered, func(i, j int) bool { return considered[i].Reference < considered[j].Reference })

	// Feasible options per booking: enough seats under its own accepted terms,
	// clear of the proposed closure, applied closures and fixed bookings.
	options := seatingOptions(rest)
	cands := make([][]candidate, len(considered))
	for i, res := range considered {
		start, end := res.StartsAt, res.endsAt()
		for _, opt := range options {
			capacity := res.Terms.capacity(opt)
			if capacity < res.PartySize {
				continue
			}
			if (closure.overlaps(start, end) && sharesTable([]string{closure.TableID}, opt)) || st.setBusy(rest, opt, start, end, inPlan) {
				continue
			}
			cands[i] = append(cands[i], candidate{option: opt, changed: !sameTableSet(opt, res.TableIDs), unused: capacity - res.PartySize})
		}
	}
	best, ok := solvePlan(considered, cands)
	if !ok {
		writeError(w, fail(http.StatusConflict, codeNoFeasiblePlan, "no seating arrangement satisfies every booking"))
		return
	}

	plan := &Plan{ID: st.newPlanID(), RestaurantID: rest.ID, RestaurantRevision: rest.Revision, Closure: closure, Assignments: []Assignment{}}
	for i, res := range considered {
		c := best[i]
		plan.Assignments = append(plan.Assignments, Assignment{ReservationID: res.ID, Reference: res.Reference, TableIDs: c.option, Changed: c.changed})
		if c.changed {
			plan.MovedCount++
		}
		plan.UnusedSeats += c.unused
	}
	closure.PlanID = plan.ID
	plan.Closure = closure
	st.addPlan(plan)
	resp, _ := json.Marshal(planViewOf(plan))
	st.idem[k] = &IdemRecord{idemKey: k, Body: canonical, Status: http.StatusCreated, Response: resp}
	writeRaw(w, http.StatusCreated, resp)
}

func (a *app) applyReplan(w http.ResponseWriter, r *http.Request, restaurantID, planID string) {
	body, e := readBody(w, r, maxBodyBytes)
	if e != nil {
		writeError(w, e)
		return
	}
	a.store.mu.Lock()
	defer a.store.mu.Unlock()
	st := a.store.state
	_, rest, k, canonical, handled := managerWrite(w, r, st, body, restaurantID, replansPath(restaurantID)+"/"+planID+"/apply")
	if handled {
		return
	}
	plan := st.plansByID[planID]
	if plan == nil || plan.RestaurantID != rest.ID {
		writeError(w, notFound("no such plan at this restaurant"))
		return
	}
	if plan.Applied {
		writeError(w, fail(http.StatusConflict, codePlanAlreadyApplied, "the plan has already been applied"))
		return
	}
	if plan.RestaurantRevision != rest.Revision {
		writeError(w, fail(http.StatusConflict, codeStalePlan, "the restaurant has changed since the plan was made"))
		return
	}

	// The restaurant is exactly as it was at preview, so the plan still holds.
	at := stamp()
	touched := map[*Series]bool{}
	views := make([]reservationView, 0, len(plan.Assignments))
	for _, asg := range plan.Assignments {
		res := st.reservationByID[asg.ReservationID]
		if asg.Changed {
			old := res.TableIDs
			res.TableIDs = append([]string{}, asg.TableIDs...)
			res.Revision++
			h := res.record(eventReassigned, at, []Change{reassignmentChange(old, res.TableIDs)})
			h.PlanID = plan.ID
			if sr := st.seriesByID[res.SeriesID]; sr != nil {
				touched[sr] = true
			}
		}
		views = append(views, viewOf(res))
	}
	rest.Closures = append(rest.Closures, plan.Closure)
	rest.Revision++
	for sr := range touched {
		sr.Revision++ // exception flags, dates and identities are left as they are
	}
	plan.Applied = true
	resp, _ := json.Marshal(map[string]any{"plan_id": plan.ID, "restaurant_revision": rest.Revision, "reservations": views})
	st.idem[k] = &IdemRecord{idemKey: k, Body: canonical, Status: http.StatusCreated, Response: resp}
	writeRaw(w, http.StatusCreated, resp)
}
