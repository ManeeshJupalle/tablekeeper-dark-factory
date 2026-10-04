package main

import (
	"net/http"
	"regexp"
	"sync"
	"time"
)

// Fixture and import decoding. Both build a complete State from a decoded JSON
// tree and report the first problem as 422 validation_failed, so the live state
// is only ever replaced by a state that passed every check.

var referenceRE = regexp.MustCompile(`^[A-Z0-9]{6,12}$`)

type obj = map[string]any

func objectAt(v any, what string) (obj, *apiError) {
	o, ok := v.(map[string]any)
	if !ok {
		return nil, invalid(what + " must be an object")
	}
	return o, nil
}

// arrayField returns an array-valued field, treating an absent field as empty.
func arrayField(o obj, key string) ([]any, *apiError) {
	v, present := o[key]
	if !present {
		return []any{}, nil
	}
	arr, ok := v.([]any)
	if !ok {
		return nil, invalid(key + " must be an array")
	}
	return arr, nil
}

func requiredString(o obj, key string) (string, *apiError) {
	s, present, ok := stringField(o, key)
	if !ok {
		return "", invalid(key + " must be a string")
	}
	if !present {
		return "", invalid(key + " is required")
	}
	return s, nil
}

func optionalString(o obj, key string) (string, *apiError) {
	s, _, ok := stringField(o, key)
	if !ok {
		return "", invalid(key + " must be a string")
	}
	return s, nil
}

func requiredInt(o obj, key string, min int64) (int, *apiError) {
	v, present := o[key]
	if !present {
		return 0, invalid(key + " is required")
	}
	n, ok := jsonInt(v)
	if !ok || n < min || n > int64(int(^uint(0)>>1)) {
		return 0, invalid(key + " must be an integer")
	}
	return int(n), nil
}

func identifier(o obj, key string) (string, *apiError) {
	id, e := requiredString(o, key)
	if e != nil {
		return "", e
	}
	if id == "" || len(id) > maxIDLength {
		return "", invalid(key + " must be 1 to 64 characters")
	}
	return id, nil
}

// parseRestaurant reads a fixture restaurant. With imported set it also reads
// the stage-3 records an export carries (revision, published policies); exports
// from earlier stages simply lack them.
func parseRestaurant(v any, imported bool) (*Restaurant, *apiError) {
	o, e := objectAt(v, "restaurant")
	if e != nil {
		return nil, e
	}
	r := &Restaurant{Base: &Terms{}, Revision: initialRevision}
	if r.ID, e = identifier(o, "id"); e != nil {
		return nil, e
	}
	if r.Name, e = optionalString(o, "name"); e != nil {
		return nil, e
	}
	if r.Timezone, e = requiredString(o, "timezone"); e != nil {
		return nil, e
	}
	loc, err := time.LoadLocation(r.Timezone)
	if err != nil || r.Timezone == "" || r.Timezone == "Local" {
		return nil, invalid("timezone must be an IANA zone name")
	}
	r.Location = loc
	if r.Base.SlotMinutes, e = requiredInt(o, "slot_minutes", 1); e != nil {
		return nil, e
	}
	if r.Base.DurationMinutes, e = requiredInt(o, "reservation_duration_minutes", 1); e != nil {
		return nil, e
	}
	if r.Base.CutoffMinutes, e = requiredInt(o, "cancellation_cutoff_minutes", 0); e != nil {
		return nil, e
	}
	hours, e := arrayField(o, "opening_hours")
	if e != nil {
		return nil, e
	}
	for _, hv := range hours {
		h, e := parseOpeningHours(hv)
		if e != nil {
			return nil, e
		}
		r.Base.Hours = append(r.Base.Hours, h)
	}
	tables, e := arrayField(o, "tables")
	if e != nil {
		return nil, e
	}
	for _, tv := range tables {
		t, e := parseTable(tv)
		if e != nil {
			return nil, e
		}
		if r.table(t.ID) != nil {
			return nil, invalid("duplicate table id " + t.ID)
		}
		r.Tables = append(r.Tables, t)
	}
	pairs, e := arrayField(o, "combinable")
	if e != nil {
		return nil, e
	}
	for _, pv := range pairs {
		p, e := parseCombinablePair(r, pv)
		if e != nil {
			return nil, e
		}
		// Pairs are unordered: a repeated or reversed entry declares nothing new,
		// so only the first occurrence (and its order) is kept.
		if r.pair(p[0], p[1]) != nil {
			continue
		}
		r.Combinable = append(r.Combinable, p)
	}
	// Policy 0 is the fixture's own rules, table capacities included.
	r.Base.Capacities = map[string]int{}
	for _, t := range r.Tables {
		r.Base.Capacities[t.ID] = t.Capacity
	}
	managers, e := arrayField(o, "manager_user_ids")
	if e != nil {
		return nil, e
	}
	for _, mv := range managers {
		id, ok := mv.(string)
		if !ok {
			return nil, invalid("manager_user_ids must be strings")
		}
		r.Managers = append(r.Managers, id)
	}
	if imported {
		if e := readImportedRestaurant(r, o); e != nil {
			return nil, e
		}
	}
	return r, nil
}

// readImportedRestaurant restores a stage-3 export's restaurant revision and
// published policies, which must carry versions 1, 2, ... in publication order.
func readImportedRestaurant(r *Restaurant, o obj) *apiError {
	if _, present := o["revision"]; present {
		rev, e := requiredInt(o, "revision", initialRevision)
		if e != nil {
			return e
		}
		r.Revision = rev
	}
	policies, e := arrayField(o, "policies")
	if e != nil {
		return e
	}
	for _, pv := range policies {
		po, e := objectAt(pv, "policy")
		if e != nil {
			return e
		}
		p, e := parsePolicy(r, po)
		if e != nil {
			return e
		}
		version, e := requiredInt(po, "policy_version", 1)
		if e != nil {
			return e
		}
		if version != len(r.Policies)+1 {
			return invalid("policy versions must be 1, 2, ... in publication order")
		}
		p.Terms.PolicyVersion = version
		r.Policies = append(r.Policies, p)
	}
	return nil
}

// parseCombinablePair accepts exactly two distinct known table ids (stage 2).
func parseCombinablePair(r *Restaurant, v any) ([]string, *apiError) {
	arr, ok := v.([]any)
	if !ok || len(arr) != maxTablesPerBooking {
		return nil, invalid("each combinable entry must be a pair of table ids")
	}
	pair := make([]string, 0, maxTablesPerBooking)
	for _, idv := range arr {
		id, ok := idv.(string)
		if !ok {
			return nil, invalid("combinable table ids must be strings")
		}
		if r.table(id) == nil {
			return nil, invalid("combinable names an unknown table " + id)
		}
		pair = append(pair, id)
	}
	if pair[0] == pair[1] {
		return nil, invalid("a combinable pair must name two different tables")
	}
	return pair, nil
}

func parseOpeningHours(v any) (OpeningHours, *apiError) {
	o, e := objectAt(v, "opening_hours entry")
	if e != nil {
		return OpeningHours{}, e
	}
	var h OpeningHours
	if h.Weekday, e = requiredString(o, "weekday"); e != nil {
		return h, e
	}
	if !validWeekday(h.Weekday) {
		return h, invalid("weekday must be one of mon tue wed thu fri sat sun")
	}
	if h.Opens, e = requiredString(o, "opens"); e != nil {
		return h, e
	}
	if h.Closes, e = requiredString(o, "closes"); e != nil {
		return h, e
	}
	var ok bool
	if h.opensMin, ok = parseHHMM(h.Opens); !ok {
		return h, invalid("opens must be HH:MM")
	}
	if h.closesMin, ok = parseHHMM(h.Closes); !ok {
		return h, invalid("closes must be HH:MM")
	}
	if h.closesMin <= h.opensMin {
		return h, invalid("closes must be later than opens")
	}
	return h, nil
}

func parseTable(v any) (Table, *apiError) {
	o, e := objectAt(v, "table")
	if e != nil {
		return Table{}, e
	}
	var t Table
	if t.ID, e = identifier(o, "id"); e != nil {
		return t, e
	}
	if t.Label, e = optionalString(o, "label"); e != nil {
		return t, e
	}
	if t.Capacity, e = requiredInt(o, "capacity", 1); e != nil {
		return t, e
	}
	return t, nil
}

func addRestaurants(st *State, v []any, imported bool) *apiError {
	for _, rv := range v {
		r, e := parseRestaurant(rv, imported)
		if e != nil {
			return e
		}
		if st.restaurantsByID[r.ID] != nil {
			return invalid("duplicate restaurant id " + r.ID)
		}
		st.addRestaurant(r)
	}
	return nil
}

// addUser validates the shared user fields and registers the account.
func addUser(st *State, o obj, passwordHash string) *apiError {
	u := &User{PasswordHash: passwordHash}
	var e *apiError
	if u.ID, e = identifier(o, "id"); e != nil {
		return e
	}
	if u.Email, e = requiredString(o, "email"); e != nil {
		return e
	}
	if u.Email == "" {
		return invalid("email must not be empty")
	}
	if u.DisplayName, e = optionalString(o, "display_name"); e != nil {
		return e
	}
	if st.usersByID[u.ID] != nil {
		return invalid("duplicate user id " + u.ID)
	}
	if st.usersByEmail[emailKey(u.Email)] != nil {
		return invalid("duplicate user email " + u.Email)
	}
	st.addUser(u)
	return nil
}

// addReservation validates a seeded or imported booking: the create-body fields
// plus id, reference and user_id; status and created_at are optional. A seeded
// booking starts at revision 1 under policy 0 with a created history entry; with
// imported set, a stage-3 export's revision, accepted terms and history are kept.
func addReservation(st *State, v any, createdAt time.Time, imported bool) *apiError {
	o, e := objectAt(v, "reservation")
	if e != nil {
		return e
	}
	res := &Reservation{Status: statusConfirmed, CreatedAt: createdAt, Revision: initialRevision}
	if res.ID, e = identifier(o, "id"); e != nil {
		return e
	}
	if res.Reference, e = requiredString(o, "reference"); e != nil {
		return e
	}
	if !referenceRE.MatchString(res.Reference) {
		return invalid("reference must be 6 to 12 characters of A-Z0-9")
	}
	if res.UserID, e = requiredString(o, "user_id"); e != nil {
		return e
	}
	if res.RestaurantID, e = requiredString(o, "restaurant_id"); e != nil {
		return e
	}
	ids, e := parseTableSet(o)
	if e != nil {
		return e
	}
	if ids == nil {
		return invalid("reservation needs table_id or table_ids")
	}
	local, e := requiredString(o, "starts_at_local")
	if e != nil {
		return e
	}
	if res.PartySize, e = requiredInt(o, "party_size", 1); e != nil {
		return e
	}
	if status, e := optionalString(o, "status"); e != nil {
		return e
	} else if status != "" {
		if status != statusConfirmed && status != statusCancelled {
			return invalid("status must be confirmed or cancelled")
		}
		res.Status = status
	}
	if created, e := optionalString(o, "created_at"); e != nil {
		return e
	} else if created != "" {
		t, err := time.Parse(time.RFC3339, created)
		if err != nil {
			return invalid("created_at must be RFC 3339")
		}
		res.CreatedAt = t.UTC()
	}
	if st.usersByID[res.UserID] == nil {
		return invalid("reservation user_id is unknown")
	}
	rest := st.restaurantsByID[res.RestaurantID]
	if rest == nil {
		return invalid("reservation restaurant_id is unknown")
	}
	// Seeded bookings are given, so only the set itself is checked: known,
	// distinct tables, at most a pair. A declared pair is stored in its order.
	seen := map[string]bool{}
	for _, id := range ids {
		if rest.table(id) == nil {
			return invalid("reservation names an unknown table " + id)
		}
		if seen[id] {
			return invalid("reservation repeats table " + id)
		}
		seen[id] = true
	}
	if len(ids) > maxTablesPerBooking {
		return invalid("a reservation may occupy at most two tables")
	}
	res.TableIDs = append([]string{}, ids...)
	if len(ids) == maxTablesPerBooking {
		if p := rest.pair(ids[0], ids[1]); p != nil {
			res.TableIDs = []string{p[0], p[1]}
		}
	}
	lt, ok := parseLocalTime(local)
	if !ok {
		return invalid("starts_at_local must be YYYY-MM-DDTHH:MM")
	}
	start, ok := resolveLocal(rest.Location, lt)
	if !ok {
		return invalid("starts_at_local does not exist in the restaurant's zone")
	}
	res.StartsAtLocal, res.StartsAt = lt, start
	if st.reservationByID[res.ID] != nil {
		return invalid("duplicate reservation id " + res.ID)
	}
	if st.reservationByRf[res.Reference] != nil {
		return invalid("duplicate reservation reference " + res.Reference)
	}
	res.Terms = rest.Base
	if imported {
		if e := readImportedReservation(rest, res, o); e != nil {
			return e
		}
	}
	if res.History == nil {
		res.History = []*HistoryEntry{{
			Seq: 1, At: res.CreatedAt, Event: eventCreated, Changes: creationChanges(res),
			Revision: initialRevision, Terms: res.Terms,
		}}
	}
	st.addReservation(res)
	return nil
}

// importedTerms resolves an exported accepted_terms object by its policy
// version; policies are immutable, so the version identifies the snapshot.
func importedTerms(rest *Restaurant, v any) (*Terms, *apiError) {
	o, e := objectAt(v, "accepted_terms")
	if e != nil {
		return nil, e
	}
	version, e := requiredInt(o, "policy_version", 0)
	if e != nil {
		return nil, e
	}
	t := rest.termsByVersion(version)
	if t == nil {
		return nil, invalid("accepted_terms names an unknown policy version")
	}
	return t, nil
}

// readImportedReservation restores the stage-3 fields of an exported booking.
func readImportedReservation(rest *Restaurant, res *Reservation, o obj) *apiError {
	var e *apiError
	if _, present := o["revision"]; present {
		if res.Revision, e = requiredInt(o, "revision", initialRevision); e != nil {
			return e
		}
	}
	if tv, present := o["accepted_terms"]; present {
		if res.Terms, e = importedTerms(rest, tv); e != nil {
			return e
		}
	}
	if _, present := o["history"]; !present {
		return nil
	}
	entries, e := arrayField(o, "history")
	if e != nil {
		return e
	}
	res.History = []*HistoryEntry{}
	for i, hv := range entries {
		h, e := parseHistoryEntry(rest, hv)
		if e != nil {
			return e
		}
		if h.Seq != i+1 {
			return invalid("history seq must start at 1 and increase by 1")
		}
		res.History = append(res.History, h)
	}
	if len(res.History) == 0 {
		return invalid("history must contain the created entry")
	}
	return nil
}

func parseHistoryEntry(rest *Restaurant, v any) (*HistoryEntry, *apiError) {
	o, e := objectAt(v, "history entry")
	if e != nil {
		return nil, e
	}
	h := &HistoryEntry{}
	if h.Seq, e = requiredInt(o, "seq", 1); e != nil {
		return nil, e
	}
	at, e := requiredString(o, "at")
	if e != nil {
		return nil, e
	}
	t, err := time.Parse(time.RFC3339, at)
	if err != nil {
		return nil, invalid("history at must be RFC 3339")
	}
	h.At = t.UTC()
	if h.Event, e = requiredString(o, "event"); e != nil {
		return nil, e
	}
	if h.Event != eventCreated && h.Event != eventChanged && h.Event != eventCancelled {
		return nil, invalid("unknown history event " + h.Event)
	}
	changes, e := arrayField(o, "changes")
	if e != nil {
		return nil, e
	}
	h.Changes = []Change{}
	for _, cv := range changes {
		co, e := objectAt(cv, "history change")
		if e != nil {
			return nil, e
		}
		field, e := requiredString(co, "field")
		if e != nil {
			return nil, e
		}
		h.Changes = append(h.Changes, Change{Field: field, From: co["from"], To: co["to"]})
	}
	if h.Revision, e = requiredInt(o, "revision", initialRevision); e != nil {
		return nil, e
	}
	if h.Terms, e = importedTerms(rest, o["accepted_terms"]); e != nil {
		return nil, e
	}
	return h, nil
}

// stateFromFixture builds the state POST /_test/reset installs (§4).
func stateFromFixture(fixture obj, now time.Time) (*State, *apiError) {
	st := newState()
	users, e := arrayField(fixture, "users")
	if e != nil {
		return nil, e
	}
	// Fixture passwords arrive in plaintext and are hashed like any signup.
	// Hashing is the slow part, so it runs concurrently before validation.
	passwords := make([]string, len(users))
	userObjs := make([]obj, len(users))
	for i, uv := range users {
		o, e := objectAt(uv, "user")
		if e != nil {
			return nil, e
		}
		pw, e := requiredString(o, "password")
		if e != nil {
			return nil, e
		}
		userObjs[i], passwords[i] = o, pw
	}
	hashes := make([]string, len(users))
	var wg sync.WaitGroup
	for i, pw := range passwords {
		wg.Add(1)
		go func(i int, pw string) {
			defer wg.Done()
			hashes[i], _ = hashPassword(pw)
		}(i, pw)
	}
	wg.Wait()
	for i, o := range userObjs {
		if hashes[i] == "" {
			return nil, fail(http.StatusInternalServerError, codeInternal, "password hashing failed")
		}
		if e := addUser(st, o, hashes[i]); e != nil {
			return nil, e
		}
	}
	restaurants, e := arrayField(fixture, "restaurants")
	if e != nil {
		return nil, e
	}
	if e := addRestaurants(st, restaurants, false); e != nil {
		return nil, e
	}
	reservations, e := arrayField(fixture, "reservations")
	if e != nil {
		return nil, e
	}
	for _, rv := range reservations {
		if e := addReservation(st, rv, now, false); e != nil {
			return nil, e
		}
	}
	return st, nil
}
