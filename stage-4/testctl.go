package main

import (
	"encoding/json"
	"net/http"
	"time"
)

// Test-control endpoints (§3.3, §10): reset, export and import. Each replaces or
// snapshots the whole state in one step under the store lock.

const (
	trackName     = "tablekeeper"
	formatVersion = 1
)

func (a *app) reset(w http.ResponseWriter, r *http.Request) {
	body, e := readBody(w, r, maxTestBodyBytes)
	if e != nil {
		writeError(w, e)
		return
	}
	fixture, e := parseJSONObject(body)
	if e != nil {
		writeError(w, e)
		return
	}
	st, e := stateFromFixture(fixture, stamp())
	if e != nil {
		writeError(w, e)
		return
	}
	a.store.mu.Lock()
	a.store.state = st
	a.store.mu.Unlock()
	writeNoContent(w)
}

type exportEnvelope struct {
	Track         string      `json:"track"`
	FormatVersion int         `json:"format_version"`
	State         exportState `json:"state"`
}

type exportState struct {
	Users        []exportUser        `json:"users"`
	Tokens       []exportToken       `json:"tokens"`
	Restaurants  []exportRestaurant  `json:"restaurants"`
	Reservations []exportReservation `json:"reservations"`
	Series       []exportSeries      `json:"series"`
	Plans        []exportPlan        `json:"plans"`
	Idempotency  []exportIdem        `json:"idempotency"`
}

type exportClosure struct {
	TableID string `json:"table_id"`
	From    string `json:"from"`
	To      string `json:"to"`
	PlanID  string `json:"plan_id"`
}

type exportAssignment struct {
	ReservationID string   `json:"reservation_id"`
	Reference     string   `json:"reference"`
	TableIDs      []string `json:"table_ids"`
	Changed       bool     `json:"changed"`
}

type exportPlan struct {
	ID                 string             `json:"id"`
	RestaurantID       string             `json:"restaurant_id"`
	RestaurantRevision int                `json:"restaurant_revision"`
	Closure            exportClosure      `json:"closure"`
	Assignments        []exportAssignment `json:"assignments"`
	MovedCount         int                `json:"moved_count"`
	UnusedSeats        int                `json:"unused_seats"`
	Applied            bool               `json:"applied"`
}

func exportClosureOf(c Closure) exportClosure {
	return exportClosure{TableID: c.TableID, From: c.FromRaw, To: c.ToRaw, PlanID: c.PlanID}
}

// exportRestaurant is the fixture configuration plus revision, policies and
// applied closures.
type exportRestaurant struct {
	restaurantDetail
	Policies []policyView    `json:"policies"`
	Closures []exportClosure `json:"closures"`
}

type exportOccurrence struct {
	Index         int    `json:"index"`
	ReservationID string `json:"reservation_id"`
	Exception     bool   `json:"exception"`
}

type exportSeries struct {
	ID            string             `json:"id"`
	UserID        string             `json:"user_id"`
	RestaurantID  string             `json:"restaurant_id"`
	Revision      int                `json:"revision"`
	IntervalWeeks int                `json:"interval_weeks"`
	Occurrences   []exportOccurrence `json:"occurrences"`
}

type exportUser struct {
	ID           string `json:"id"`
	Email        string `json:"email"`
	DisplayName  string `json:"display_name"`
	PasswordHash string `json:"password_hash"`
}

type exportToken struct {
	Token  string `json:"token"`
	UserID string `json:"user_id"`
}

type exportReservation struct {
	ID            string             `json:"id"`
	Reference     string             `json:"reference"`
	UserID        string             `json:"user_id"`
	RestaurantID  string             `json:"restaurant_id"`
	TableIDs      []string           `json:"table_ids"`
	PartySize     int                `json:"party_size"`
	Status        string             `json:"status"`
	StartsAtLocal string             `json:"starts_at_local"`
	StartsAt      string             `json:"starts_at"`
	CreatedAt     string             `json:"created_at"`
	Revision      int                `json:"revision"`
	AcceptedTerms termsView          `json:"accepted_terms"`
	History       []historyEntryView `json:"history"`
}

type exportIdem struct {
	UserID   string          `json:"user_id"`
	Method   string          `json:"method"`
	Path     string          `json:"path"`
	Key      string          `json:"key"`
	Body     string          `json:"body"`
	Status   int             `json:"status"`
	Response json.RawMessage `json:"response"`
}

func snapshot(st *State) exportEnvelope {
	out := exportState{
		Users:        []exportUser{},
		Tokens:       []exportToken{},
		Restaurants:  []exportRestaurant{},
		Reservations: []exportReservation{},
		Series:       []exportSeries{},
		Plans:        []exportPlan{},
		Idempotency:  []exportIdem{},
	}
	for _, u := range st.users {
		out.Users = append(out.Users, exportUser{u.ID, u.Email, u.DisplayName, u.PasswordHash})
	}
	// Tokens are keyed by user so the export is stable and complete.
	for _, u := range st.users {
		for tok, uid := range st.tokens {
			if uid == u.ID {
				out.Tokens = append(out.Tokens, exportToken{tok, uid})
			}
		}
	}
	for _, r := range st.restaurants {
		er := exportRestaurant{restaurantDetail: detailOf(r), Policies: []policyView{}, Closures: []exportClosure{}}
		for _, p := range r.Policies {
			er.Policies = append(er.Policies, policyViewOf(p))
		}
		for _, c := range r.Closures {
			er.Closures = append(er.Closures, exportClosureOf(c))
		}
		out.Restaurants = append(out.Restaurants, er)
	}
	for _, p := range st.plans {
		ep := exportPlan{ID: p.ID, RestaurantID: p.RestaurantID, RestaurantRevision: p.RestaurantRevision,
			Closure: exportClosureOf(p.Closure), Assignments: []exportAssignment{},
			MovedCount: p.MovedCount, UnusedSeats: p.UnusedSeats, Applied: p.Applied}
		for _, a := range p.Assignments {
			ep.Assignments = append(ep.Assignments, exportAssignment{a.ReservationID, a.Reference, append([]string{}, a.TableIDs...), a.Changed})
		}
		out.Plans = append(out.Plans, ep)
	}
	for _, sr := range st.series {
		es := exportSeries{ID: sr.ID, UserID: sr.UserID, RestaurantID: sr.RestaurantID,
			Revision: sr.Revision, IntervalWeeks: sr.IntervalWeeks, Occurrences: []exportOccurrence{}}
		for _, o := range sr.Occurrences {
			es.Occurrences = append(es.Occurrences, exportOccurrence{o.Index, o.ReservationID, o.Exception})
		}
		out.Series = append(out.Series, es)
	}
	for _, res := range st.reservations {
		out.Reservations = append(out.Reservations, exportReservation{
			ID:            res.ID,
			Reference:     res.Reference,
			UserID:        res.UserID,
			RestaurantID:  res.RestaurantID,
			TableIDs:      append([]string{}, res.TableIDs...),
			PartySize:     res.PartySize,
			Status:        res.Status,
			StartsAtLocal: res.StartsAtLocal.String(),
			StartsAt:      formatTime(res.StartsAt),
			CreatedAt:     formatTime(res.CreatedAt.UTC()),
			Revision:      res.Revision,
			AcceptedTerms: termsViewOf(res.Terms),
			History:       historyViewOf(res),
		})
	}
	for _, rec := range st.idem {
		out.Idempotency = append(out.Idempotency, exportIdem{
			UserID: rec.UserID, Method: rec.Method, Path: rec.Path, Key: rec.Key,
			Body: rec.Body, Status: rec.Status, Response: rec.Response,
		})
	}
	return exportEnvelope{Track: trackName, FormatVersion: formatVersion, State: out}
}

func (a *app) export(w http.ResponseWriter, r *http.Request) {
	a.store.mu.Lock()
	body, err := json.Marshal(snapshot(a.store.state))
	a.store.mu.Unlock()
	if err != nil {
		writeError(w, fail(http.StatusInternalServerError, codeInternal, "export encoding failed"))
		return
	}
	writeRaw(w, http.StatusOK, body)
}

func (a *app) importState(w http.ResponseWriter, r *http.Request) {
	body, e := readBody(w, r, maxTestBodyBytes)
	if e != nil {
		writeError(w, e)
		return
	}
	envelope, e := parseJSONObject(body)
	if e != nil {
		writeError(w, e)
		return
	}
	st, e := stateFromExport(envelope)
	if e != nil {
		writeError(w, e)
		return
	}
	a.store.mu.Lock()
	a.store.state = st
	a.store.mu.Unlock()
	writeNoContent(w)
}

// stateFromExport rebuilds a State from an export envelope produced by this
// service or by the stage-1 or stage-2 service (same track and format_version).
// Earlier exports lack combinable, policies, revisions, terms, histories and
// series: bookings then start at revision 1 under policy 0 with a created entry
// at created_at, and restaurants at revision 1. Every record is verified so a
// bad import leaves the destination as is.
func stateFromExport(envelope obj) (*State, *apiError) {
	track, e := requiredString(envelope, "track")
	if e != nil {
		return nil, e
	}
	if track != trackName {
		return nil, invalid("track must be " + trackName)
	}
	version, e := requiredInt(envelope, "format_version", 0)
	if e != nil {
		return nil, e
	}
	if version != formatVersion {
		return nil, invalid("unsupported format_version")
	}
	stateRaw, present := envelope["state"]
	if !present {
		return nil, invalid("state is required")
	}
	state, e := objectAt(stateRaw, "state")
	if e != nil {
		return nil, e
	}

	st := newState()
	users, e := arrayField(state, "users")
	if e != nil {
		return nil, e
	}
	for _, uv := range users {
		o, e := objectAt(uv, "user")
		if e != nil {
			return nil, e
		}
		hash, e := requiredString(o, "password_hash")
		if e != nil {
			return nil, e
		}
		if hash == "" {
			return nil, invalid("password_hash must not be empty")
		}
		if e := addUser(st, o, hash); e != nil {
			return nil, e
		}
	}
	tokens, e := arrayField(state, "tokens")
	if e != nil {
		return nil, e
	}
	for _, tv := range tokens {
		o, e := objectAt(tv, "token")
		if e != nil {
			return nil, e
		}
		tok, e := requiredString(o, "token")
		if e != nil {
			return nil, e
		}
		uid, e := requiredString(o, "user_id")
		if e != nil {
			return nil, e
		}
		if tok == "" || st.usersByID[uid] == nil {
			return nil, invalid("token must be non-empty and belong to a known user")
		}
		if _, dup := st.tokens[tok]; dup {
			return nil, invalid("duplicate token")
		}
		st.tokens[tok] = uid
	}
	restaurants, e := arrayField(state, "restaurants")
	if e != nil {
		return nil, e
	}
	if e := addRestaurants(st, restaurants, true); e != nil {
		return nil, e
	}
	reservations, e := arrayField(state, "reservations")
	if e != nil {
		return nil, e
	}
	importedAt := stamp()
	for _, rv := range reservations {
		if e := addReservation(st, rv, importedAt, true); e != nil {
			return nil, e
		}
	}
	series, e := arrayField(state, "series")
	if e != nil {
		return nil, e
	}
	for _, sv := range series {
		if e := addImportedSeries(st, sv); e != nil {
			return nil, e
		}
	}
	plans, e := arrayField(state, "plans")
	if e != nil {
		return nil, e
	}
	for _, pv := range plans {
		if e := addImportedPlan(st, pv); e != nil {
			return nil, e
		}
	}
	records, e := arrayField(state, "idempotency")
	if e != nil {
		return nil, e
	}
	for _, rv := range records {
		rec, e := parseIdemRecord(st, rv)
		if e != nil {
			return nil, e
		}
		if st.idem[rec.idemKey] != nil {
			return nil, invalid("duplicate idempotency record")
		}
		st.idem[rec.idemKey] = rec
	}
	return st, nil
}

// addImportedSeries restores one exported series and links its reservations,
// which must belong to the series owner and restaurant and to no other series.
func addImportedSeries(st *State, v any) *apiError {
	o, e := objectAt(v, "series")
	if e != nil {
		return e
	}
	sr := &Series{}
	if sr.ID, e = identifier(o, "id"); e != nil {
		return e
	}
	if st.seriesByID[sr.ID] != nil {
		return invalid("duplicate series id " + sr.ID)
	}
	if sr.UserID, e = requiredString(o, "user_id"); e != nil {
		return e
	}
	if sr.RestaurantID, e = requiredString(o, "restaurant_id"); e != nil {
		return e
	}
	if sr.Revision, e = requiredInt(o, "revision", initialRevision); e != nil {
		return e
	}
	if sr.IntervalWeeks, e = seriesInt(o, "interval_weeks", minSeriesWeeks, maxSeriesWeeks); e != nil {
		return e
	}
	occurrences, e := arrayField(o, "occurrences")
	if e != nil {
		return e
	}
	if len(occurrences) < minSeriesCount || len(occurrences) > maxSeriesCount {
		return invalid("a series has 2 to 12 occurrences")
	}
	for i, ov := range occurrences {
		oo, e := objectAt(ov, "occurrence")
		if e != nil {
			return e
		}
		index, e := requiredInt(oo, "index", 0)
		if e != nil {
			return e
		}
		resID, e := requiredString(oo, "reservation_id")
		if e != nil {
			return e
		}
		exception, ok := oo["exception"].(bool)
		if !ok || index != i {
			return invalid("occurrences need consecutive indices and a boolean exception")
		}
		res := st.reservationByID[resID]
		if res == nil || res.UserID != sr.UserID || res.RestaurantID != sr.RestaurantID || res.SeriesID != "" {
			return invalid("series occurrence names an unknown or foreign reservation")
		}
		res.SeriesID = sr.ID
		sr.Occurrences = append(sr.Occurrences, &Occurrence{Index: index, ReservationID: resID, Exception: exception})
	}
	st.addSeries(sr)
	return nil
}

// parseClosure reads an exported closure of a restaurant's table.
func parseClosure(rest *Restaurant, v any) (Closure, *apiError) {
	o, e := objectAt(v, "closure")
	if e != nil {
		return Closure{}, e
	}
	c := Closure{}
	if c.TableID, e = requiredString(o, "table_id"); e != nil {
		return c, e
	}
	if rest.table(c.TableID) == nil {
		return c, invalid("closure names an unknown table")
	}
	if c.FromRaw, e = requiredString(o, "from"); e != nil {
		return c, e
	}
	if c.ToRaw, e = requiredString(o, "to"); e != nil {
		return c, e
	}
	var err error
	if c.From, err = time.Parse(time.RFC3339, c.FromRaw); err != nil {
		return c, invalid("closure from must be RFC 3339")
	}
	if c.To, err = time.Parse(time.RFC3339, c.ToRaw); err != nil {
		return c, invalid("closure to must be RFC 3339")
	}
	if !c.From.Before(c.To) {
		return c, invalid("closure from must be earlier than to")
	}
	if c.PlanID, e = optionalString(o, "plan_id"); e != nil {
		return c, e
	}
	return c, nil
}

// addImportedPlan restores a stored plan, pending or applied.
func addImportedPlan(st *State, v any) *apiError {
	o, e := objectAt(v, "plan")
	if e != nil {
		return e
	}
	p := &Plan{Assignments: []Assignment{}}
	if p.ID, e = identifier(o, "id"); e != nil {
		return e
	}
	if st.plansByID[p.ID] != nil {
		return invalid("duplicate plan id " + p.ID)
	}
	if p.RestaurantID, e = requiredString(o, "restaurant_id"); e != nil {
		return e
	}
	rest := st.restaurantsByID[p.RestaurantID]
	if rest == nil {
		return invalid("plan names an unknown restaurant")
	}
	if p.RestaurantRevision, e = requiredInt(o, "restaurant_revision", initialRestaurantRevision); e != nil {
		return e
	}
	if p.Closure, e = parseClosure(rest, o["closure"]); e != nil {
		return e
	}
	p.Closure.PlanID = p.ID
	if p.MovedCount, e = requiredInt(o, "moved_count", 0); e != nil {
		return e
	}
	if p.UnusedSeats, e = requiredInt(o, "unused_seats", 0); e != nil {
		return e
	}
	applied, ok := o["applied"].(bool)
	if !ok {
		return invalid("plan applied must be a boolean")
	}
	p.Applied = applied
	assignments, e := arrayField(o, "assignments")
	if e != nil {
		return e
	}
	for _, av := range assignments {
		ao, e := objectAt(av, "assignment")
		if e != nil {
			return e
		}
		asg := Assignment{}
		if asg.ReservationID, e = requiredString(ao, "reservation_id"); e != nil {
			return e
		}
		res := st.reservationByID[asg.ReservationID]
		if res == nil || res.RestaurantID != rest.ID {
			return invalid("plan assignment names an unknown reservation")
		}
		asg.Reference = res.Reference
		ids, e := parseTableSet(ao)
		if e != nil || ids == nil {
			return invalid("plan assignment needs table_ids")
		}
		for _, id := range ids {
			if rest.table(id) == nil {
				return invalid("plan assignment names an unknown table")
			}
		}
		asg.TableIDs = ids
		if asg.Changed, ok = ao["changed"].(bool); !ok {
			return invalid("plan assignment changed must be a boolean")
		}
		p.Assignments = append(p.Assignments, asg)
	}
	st.addPlan(p)
	return nil
}

func parseIdemRecord(st *State, v any) (*IdemRecord, *apiError) {
	o, e := objectAt(v, "idempotency record")
	if e != nil {
		return nil, e
	}
	rec := &IdemRecord{}
	if rec.UserID, e = requiredString(o, "user_id"); e != nil {
		return nil, e
	}
	if st.usersByID[rec.UserID] == nil {
		return nil, invalid("idempotency record user_id is unknown")
	}
	if rec.Method, e = requiredString(o, "method"); e != nil {
		return nil, e
	}
	if rec.Path, e = requiredString(o, "path"); e != nil {
		return nil, e
	}
	if rec.Key, e = requiredString(o, "key"); e != nil {
		return nil, e
	}
	if rec.Body, e = requiredString(o, "body"); e != nil {
		return nil, e
	}
	if rec.Status, e = requiredInt(o, "status", 100); e != nil {
		return nil, e
	}
	resp, present := o["response"]
	if !present {
		return nil, invalid("idempotency record response is required")
	}
	raw, err := json.Marshal(resp)
	if err != nil {
		return nil, invalid("idempotency record response is not valid JSON")
	}
	rec.Response = raw
	return rec, nil
}
