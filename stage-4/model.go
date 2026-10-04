package main

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"net/http"
	"strings"
	"sync"
	"time"
)

const (
	statusConfirmed = "confirmed"
	statusCancelled = "cancelled"

	maxIDLength     = 64
	referenceLength = 8
	referenceChars  = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
	randomIDBytes   = 8
	tokenBytes      = 32

	// maxTablesPerBooking is the largest table set a booking may occupy: a single
	// table or one declared combinable pair.
	maxTablesPerBooking = 2

	// initialRevision is the revision of every new reservation and series.
	initialRevision = 1

	// initialRestaurantRevision is a restaurant's revision after reset, or after
	// importing an export that carries none (stages 1 and 2).
	initialRestaurantRevision = 0
)

type User struct {
	ID           string
	Email        string
	DisplayName  string
	PasswordHash string
}

type OpeningHours struct {
	Weekday   string
	Opens     string
	Closes    string
	opensMin  int
	closesMin int
}

type Table struct {
	ID       string
	Label    string
	Capacity int // the fixture's capacity (policy 0)
}

// Terms are the booking rules of one policy: everything a booking accepts.
// Terms are immutable once created and shared by every booking that accepted them.
type Terms struct {
	PolicyVersion   int
	SlotMinutes     int
	DurationMinutes int
	CutoffMinutes   int
	Hours           []OpeningHours
	Capacities      map[string]int
}

func (t *Terms) duration() time.Duration { return time.Duration(t.DurationMinutes) * time.Minute }

func (t *Terms) cutoff() time.Duration { return time.Duration(t.CutoffMinutes) * time.Minute }

// capacity is the seating capacity of a table set under these terms.
func (t *Terms) capacity(ids []string) int {
	total := 0
	for _, id := range ids {
		total += t.Capacities[id]
	}
	return total
}

// checkHours finds the opening window containing the start and applies the slot
// grid and closing rules; a start outside every window is outside opening hours.
func (t *Terms) checkHours(lt localTime) *apiError {
	weekday := weekdayOf(lt.localDate)
	for _, h := range t.Hours {
		if h.Weekday != weekday || lt.Minute < h.opensMin || lt.Minute >= h.closesMin {
			continue
		}
		if (lt.Minute-h.opensMin)%t.SlotMinutes != 0 {
			return fail(http.StatusUnprocessableEntity, codeNotOnGrid, "starts_at_local is not on the slot grid")
		}
		if lt.Minute+t.DurationMinutes > h.closesMin {
			return fail(http.StatusUnprocessableEntity, codeOutsideHours, "the reservation would end after closing time")
		}
		return nil
	}
	return fail(http.StatusUnprocessableEntity, codeOutsideHours, "the restaurant is closed at that time")
}

// Policy is a published, immutable set of terms governing bookings whose local
// start date is on or after EffectiveFrom (stage 3).
type Policy struct {
	EffectiveFrom localDate
	Terms         *Terms
}

type Restaurant struct {
	ID         string
	Name       string
	Timezone   string
	Base       *Terms // policy 0: the fixture's own rules
	Tables     []Table
	Combinable [][]string // declared pairs, each in fixture order
	Managers   []string
	Policies   []*Policy // publication order; Policies[i] has version i+1
	Closures   []Closure // applied table closures (stage 4)
	Revision   int
	Location   *time.Location
}

func (r *Restaurant) table(id string) *Table {
	for i := range r.Tables {
		if r.Tables[i].ID == id {
			return &r.Tables[i]
		}
	}
	return nil
}

// pair returns the declared combinable pair containing exactly a and b, in its
// declared order, or nil when the two tables may not be combined.
func (r *Restaurant) pair(a, b string) []string {
	for _, p := range r.Combinable {
		if (p[0] == a && p[1] == b) || (p[0] == b && p[1] == a) {
			return p
		}
	}
	return nil
}

func (r *Restaurant) isManager(userID string) bool {
	for _, id := range r.Managers {
		if id == userID {
			return true
		}
	}
	return false
}

// termsFor selects the policy for a local date: the greatest effective_from not
// later than the date, ties broken by the greatest version; policy 0 otherwise.
func (r *Restaurant) termsFor(d localDate) *Terms {
	var best *Policy
	for _, p := range r.Policies {
		if p.EffectiveFrom.compare(d) > 0 {
			continue
		}
		if best == nil {
			best = p
			continue
		}
		if c := p.EffectiveFrom.compare(best.EffectiveFrom); c > 0 || (c == 0 && p.Terms.PolicyVersion > best.Terms.PolicyVersion) {
			best = p
		}
	}
	if best == nil {
		return r.Base
	}
	return best.Terms
}

// termsByVersion finds the terms of policy v (0 is the fixture), or nil.
func (r *Restaurant) termsByVersion(v int) *Terms {
	if v == 0 {
		return r.Base
	}
	if v < 1 || v > len(r.Policies) {
		return nil
	}
	return r.Policies[v-1].Terms
}

// sharesTable reports whether two table sets have a member in common.
func sharesTable(a, b []string) bool {
	for _, x := range a {
		for _, y := range b {
			if x == y {
				return true
			}
		}
	}
	return false
}

// sameTableSet reports whether two duplicate-free sets name the same tables.
func sameTableSet(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for _, x := range a {
		found := false
		for _, y := range b {
			if x == y {
				found = true
				break
			}
		}
		if !found {
			return false
		}
	}
	return true
}

type Reservation struct {
	ID            string
	Reference     string
	UserID        string
	RestaurantID  string
	TableIDs      []string // one table, or a declared combinable pair
	PartySize     int
	Status        string
	StartsAtLocal localTime
	StartsAt      time.Time
	CreatedAt     time.Time
	Terms         *Terms // accepted terms (stage 3)
	Revision      int
	History       []*HistoryEntry
	SeriesID      string // empty unless adopted into a recurring series
}

// endsAt uses the booking's own accepted duration, never a later policy's.
func (r *Reservation) endsAt() time.Time { return r.StartsAt.Add(r.Terms.duration()) }

// idemKey scopes an idempotency record to one user, method and path (§7).
type idemKey struct {
	UserID string
	Method string
	Path   string
	Key    string
}

// IdemRecord is the completed outcome of a first use, replayed verbatim.
type IdemRecord struct {
	idemKey
	Body     string // canonical JSON of the original request body
	Status   int
	Response json.RawMessage
}

// State is everything the service knows. It is replaced wholesale by reset and
// import and only ever mutated under Store.mu.
type State struct {
	users           []*User
	usersByID       map[string]*User
	usersByEmail    map[string]*User
	tokens          map[string]string // token -> user id
	restaurants     []*Restaurant
	restaurantsByID map[string]*Restaurant
	reservations    []*Reservation
	reservationByID map[string]*Reservation
	reservationByRf map[string]*Reservation
	idem            map[idemKey]*IdemRecord
	series          []*Series
	seriesByID      map[string]*Series
	plans           []*Plan
	plansByID       map[string]*Plan
}

func newState() *State {
	return &State{
		usersByID:       map[string]*User{},
		usersByEmail:    map[string]*User{},
		tokens:          map[string]string{},
		restaurantsByID: map[string]*Restaurant{},
		reservationByID: map[string]*Reservation{},
		reservationByRf: map[string]*Reservation{},
		idem:            map[idemKey]*IdemRecord{},
		seriesByID:      map[string]*Series{},
		plansByID:       map[string]*Plan{},
	}
}

func emailKey(email string) string { return strings.ToLower(email) }

func (s *State) addUser(u *User) {
	s.users = append(s.users, u)
	s.usersByID[u.ID] = u
	s.usersByEmail[emailKey(u.Email)] = u
}

func (s *State) addRestaurant(r *Restaurant) {
	s.restaurants = append(s.restaurants, r)
	s.restaurantsByID[r.ID] = r
}

func (s *State) addReservation(r *Reservation) {
	s.reservations = append(s.reservations, r)
	s.reservationByID[r.ID] = r
	s.reservationByRf[r.Reference] = r
}

func (s *State) addPlan(p *Plan) {
	s.plans = append(s.plans, p)
	s.plansByID[p.ID] = p
}

func (s *State) addSeries(sr *Series) {
	s.series = append(s.series, sr)
	s.seriesByID[sr.ID] = sr
}

func randomHex(n int) string {
	b := make([]byte, n)
	if _, err := rand.Read(b); err != nil {
		panic("crypto/rand unavailable: " + err.Error())
	}
	return hex.EncodeToString(b)
}

func randomReference() string {
	b := make([]byte, referenceLength)
	if _, err := rand.Read(b); err != nil {
		panic("crypto/rand unavailable: " + err.Error())
	}
	out := make([]byte, referenceLength)
	for i, v := range b {
		out[i] = referenceChars[int(v)%len(referenceChars)]
	}
	return string(out)
}

func (s *State) newUserID() string {
	for {
		id := "u_" + randomHex(randomIDBytes)
		if s.usersByID[id] == nil {
			return id
		}
	}
}

func (s *State) newReservationID() string {
	for {
		id := "res_" + randomHex(randomIDBytes)
		if s.reservationByID[id] == nil {
			return id
		}
	}
}

func (s *State) newReference() string {
	for {
		ref := randomReference()
		if s.reservationByRf[ref] == nil {
			return ref
		}
	}
}

func (s *State) newSeriesID() string {
	for {
		id := "ser_" + randomHex(randomIDBytes)
		if s.seriesByID[id] == nil {
			return id
		}
	}
}

func (s *State) newPlanID() string {
	for {
		id := "plan_" + randomHex(randomIDBytes)
		if s.plansByID[id] == nil {
			return id
		}
	}
}

func (s *State) newToken() string {
	for {
		tok := randomHex(tokenBytes)
		if _, taken := s.tokens[tok]; !taken {
			return tok
		}
	}
}

// Store holds the live state behind one mutex. Every handler takes the lock for
// the whole of its decision and write, which is what makes concurrent bookings
// and idempotent retries safe.
type Store struct {
	mu    sync.Mutex
	state *State
}

func NewStore() *Store { return &Store{state: newState()} }

// stamp is the timestamp recorded for writes: UTC, whole seconds, so that a
// creation and a change in the same second still order by seq and by `at`.
func stamp() time.Time { return time.Now().UTC().Truncate(time.Second) }
