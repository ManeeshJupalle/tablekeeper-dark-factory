package main

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
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
	Capacity int
}

// maxTablesPerBooking is the largest table set a booking may occupy: a single
// table or one declared combinable pair.
const maxTablesPerBooking = 2

type Restaurant struct {
	ID              string
	Name            string
	Timezone        string
	SlotMinutes     int
	DurationMinutes int
	CutoffMinutes   int
	Hours           []OpeningHours
	Tables          []Table
	Combinable      [][]string // declared pairs, each in fixture order
	Location        *time.Location
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

func (r *Restaurant) duration() time.Duration {
	return time.Duration(r.DurationMinutes) * time.Minute
}

func (r *Restaurant) cutoff() time.Duration {
	return time.Duration(r.CutoffMinutes) * time.Minute
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
}

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
