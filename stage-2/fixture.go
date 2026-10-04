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

func parseRestaurant(v any) (*Restaurant, *apiError) {
	o, e := objectAt(v, "restaurant")
	if e != nil {
		return nil, e
	}
	r := &Restaurant{}
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
	if r.SlotMinutes, e = requiredInt(o, "slot_minutes", 1); e != nil {
		return nil, e
	}
	if r.DurationMinutes, e = requiredInt(o, "reservation_duration_minutes", 1); e != nil {
		return nil, e
	}
	if r.CutoffMinutes, e = requiredInt(o, "cancellation_cutoff_minutes", 0); e != nil {
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
		r.Hours = append(r.Hours, h)
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
		r.Combinable = append(r.Combinable, p)
	}
	return r, nil
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

func addRestaurants(st *State, v []any) *apiError {
	for _, rv := range v {
		r, e := parseRestaurant(rv)
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
// plus id, reference and user_id; status and created_at are optional.
func addReservation(st *State, v any, now time.Time) *apiError {
	o, e := objectAt(v, "reservation")
	if e != nil {
		return e
	}
	res := &Reservation{Status: statusConfirmed, CreatedAt: now}
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
	st.addReservation(res)
	return nil
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
	if e := addRestaurants(st, restaurants); e != nil {
		return nil, e
	}
	reservations, e := arrayField(fixture, "reservations")
	if e != nil {
		return nil, e
	}
	for _, rv := range reservations {
		if e := addReservation(st, rv, now); e != nil {
			return nil, e
		}
	}
	return st, nil
}
