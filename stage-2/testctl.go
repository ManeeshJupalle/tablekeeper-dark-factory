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
	st, e := stateFromFixture(fixture, time.Now().UTC().Truncate(time.Second))
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
	Restaurants  []restaurantDetail  `json:"restaurants"`
	Reservations []exportReservation `json:"reservations"`
	Idempotency  []exportIdem        `json:"idempotency"`
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
	ID            string `json:"id"`
	Reference     string `json:"reference"`
	UserID        string `json:"user_id"`
	RestaurantID  string `json:"restaurant_id"`
	TableID       string `json:"table_id"`
	PartySize     int    `json:"party_size"`
	Status        string `json:"status"`
	StartsAtLocal string `json:"starts_at_local"`
	StartsAt      string `json:"starts_at"`
	CreatedAt     string `json:"created_at"`
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
		Restaurants:  []restaurantDetail{},
		Reservations: []exportReservation{},
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
		out.Restaurants = append(out.Restaurants, detailOf(r))
	}
	for _, res := range st.reservations {
		out.Reservations = append(out.Reservations, exportReservation{
			ID:            res.ID,
			Reference:     res.Reference,
			UserID:        res.UserID,
			RestaurantID:  res.RestaurantID,
			TableID:       res.TableID,
			PartySize:     res.PartySize,
			Status:        res.Status,
			StartsAtLocal: res.StartsAtLocal.String(),
			StartsAt:      formatTime(res.StartsAt),
			CreatedAt:     formatTime(res.CreatedAt.UTC()),
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
// service, verifying every record so a bad import leaves the destination as is.
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
	if e := addRestaurants(st, restaurants); e != nil {
		return nil, e
	}
	reservations, e := arrayField(state, "reservations")
	if e != nil {
		return nil, e
	}
	now := time.Now().UTC().Truncate(time.Second)
	for _, rv := range reservations {
		if e := addReservation(st, rv, now); e != nil {
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
