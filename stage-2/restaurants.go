package main

import (
	"net/http"
	"regexp"
	"strconv"
)

type restaurantSummary struct {
	ID       string `json:"id"`
	Name     string `json:"name"`
	Timezone string `json:"timezone"`
}

type openingHoursView struct {
	Weekday string `json:"weekday"`
	Opens   string `json:"opens"`
	Closes  string `json:"closes"`
}

type tableView struct {
	ID       string `json:"id"`
	Label    string `json:"label"`
	Capacity int    `json:"capacity"`
}

// restaurantDetail is the fixture's shape, used by GET /restaurants/{id} and export.
type restaurantDetail struct {
	ID                         string             `json:"id"`
	Name                       string             `json:"name"`
	Timezone                   string             `json:"timezone"`
	SlotMinutes                int                `json:"slot_minutes"`
	ReservationDurationMinutes int                `json:"reservation_duration_minutes"`
	CancellationCutoffMinutes  int                `json:"cancellation_cutoff_minutes"`
	OpeningHours               []openingHoursView `json:"opening_hours"`
	Tables                     []tableView        `json:"tables"`
}

func detailOf(r *Restaurant) restaurantDetail {
	d := restaurantDetail{
		ID:                         r.ID,
		Name:                       r.Name,
		Timezone:                   r.Timezone,
		SlotMinutes:                r.SlotMinutes,
		ReservationDurationMinutes: r.DurationMinutes,
		CancellationCutoffMinutes:  r.CutoffMinutes,
		OpeningHours:               []openingHoursView{},
		Tables:                     []tableView{},
	}
	for _, h := range r.Hours {
		d.OpeningHours = append(d.OpeningHours, openingHoursView{h.Weekday, h.Opens, h.Closes})
	}
	for _, t := range r.Tables {
		d.Tables = append(d.Tables, tableView{t.ID, t.Label, t.Capacity})
	}
	return d
}

func (a *app) listRestaurants(w http.ResponseWriter, r *http.Request) {
	a.store.mu.Lock()
	defer a.store.mu.Unlock()
	out := []restaurantSummary{}
	for _, rest := range a.store.state.restaurants {
		out = append(out, restaurantSummary{rest.ID, rest.Name, rest.Timezone})
	}
	writeJSON(w, http.StatusOK, map[string]any{"restaurants": out})
}

func (a *app) getRestaurant(w http.ResponseWriter, r *http.Request, id string) {
	a.store.mu.Lock()
	defer a.store.mu.Unlock()
	rest := a.store.state.restaurantsByID[id]
	if rest == nil {
		writeError(w, notFound("no such restaurant"))
		return
	}
	writeJSON(w, http.StatusOK, detailOf(rest))
}

type slotView struct {
	StartsAtLocal     string   `json:"starts_at_local"`
	StartsAt          string   `json:"starts_at"`
	AvailableTableIDs []string `json:"available_table_ids"`
}

type availabilityView struct {
	RestaurantID string     `json:"restaurant_id"`
	Date         string     `json:"date"`
	Timezone     string     `json:"timezone"`
	Slots        []slotView `json:"slots"`
}

var plainDigits = regexp.MustCompile(`^[0-9]+$`)

// queryInt applies §5: an integer query parameter is plain decimal digits.
func queryInt(raw string) (int, bool) {
	if !plainDigits.MatchString(raw) {
		return 0, false
	}
	n, err := strconv.Atoi(raw)
	return n, err == nil
}

func (a *app) availability(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	rid, dateRaw, partyRaw := q.Get("restaurant_id"), q.Get("date"), q.Get("party_size")
	if rid == "" || dateRaw == "" || partyRaw == "" {
		writeError(w, invalid("restaurant_id, date and party_size are required"))
		return
	}
	date, ok := parseLocalDate(dateRaw)
	if !ok {
		writeError(w, invalid("date must be a valid YYYY-MM-DD"))
		return
	}
	party, ok := queryInt(partyRaw)
	if !ok || party < 1 {
		writeError(w, invalid("party_size must be a positive integer"))
		return
	}

	a.store.mu.Lock()
	defer a.store.mu.Unlock()
	st := a.store.state
	rest := st.restaurantsByID[rid]
	if rest == nil {
		writeError(w, notFound("no such restaurant"))
		return
	}
	writeJSON(w, http.StatusOK, availabilityView{
		RestaurantID: rest.ID,
		Date:         date.String(),
		Timezone:     rest.Timezone,
		Slots:        st.slotsFor(rest, date, party),
	})
}

// slotsFor lists every grid slot of the day (§8) with the tables that can seat
// the party and are free for the whole reservation. Caller holds the lock.
func (s *State) slotsFor(rest *Restaurant, date localDate, party int) []slotView {
	slots := []slotView{}
	weekday := weekdayOf(date)
	for _, h := range rest.Hours {
		if h.Weekday != weekday {
			continue
		}
		for minute := h.opensMin; minute+rest.DurationMinutes <= h.closesMin; minute += rest.SlotMinutes {
			lt := localTime{date, minute}
			start, ok := resolveLocal(rest.Location, lt)
			if !ok {
				continue // the slot falls in a spring-forward gap
			}
			end := start.Add(rest.duration())
			free := []string{}
			for _, t := range rest.Tables {
				if t.Capacity >= party && !s.tableBusy(rest, t.ID, start, end, nil) {
					free = append(free, t.ID)
				}
			}
			slots = append(slots, slotView{lt.String(), formatTime(start), free})
		}
	}
	return slots
}
