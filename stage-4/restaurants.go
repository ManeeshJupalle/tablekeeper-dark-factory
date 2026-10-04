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

// restaurantDetail is the original fixture configuration (policy 0), used by
// GET /restaurants/{id} and export, plus the restaurant revision.
type restaurantDetail struct {
	ID                         string             `json:"id"`
	Name                       string             `json:"name"`
	Timezone                   string             `json:"timezone"`
	SlotMinutes                int                `json:"slot_minutes"`
	ReservationDurationMinutes int                `json:"reservation_duration_minutes"`
	CancellationCutoffMinutes  int                `json:"cancellation_cutoff_minutes"`
	OpeningHours               []openingHoursView `json:"opening_hours"`
	Tables                     []tableView        `json:"tables"`
	Combinable                 [][]string         `json:"combinable"`
	ManagerUserIDs             []string           `json:"manager_user_ids"`
	Revision                   int                `json:"revision"`
}

func detailOf(r *Restaurant) restaurantDetail {
	d := restaurantDetail{
		ID:                         r.ID,
		Name:                       r.Name,
		Timezone:                   r.Timezone,
		SlotMinutes:                r.Base.SlotMinutes,
		ReservationDurationMinutes: r.Base.DurationMinutes,
		CancellationCutoffMinutes:  r.Base.CutoffMinutes,
		OpeningHours:               hoursView(r.Base.Hours),
		Tables:                     []tableView{},
		Combinable:                 [][]string{},
		ManagerUserIDs:             append([]string{}, r.Managers...),
		Revision:                   r.Revision,
	}
	for _, t := range r.Tables {
		d.Tables = append(d.Tables, tableView{t.ID, t.Label, t.Capacity})
	}
	for _, p := range r.Combinable {
		d.Combinable = append(d.Combinable, []string{p[0], p[1]})
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

// optionView is one bookable seating option: a single table or a declared pair.
type optionView struct {
	TableIDs []string `json:"table_ids"`
	Capacity int      `json:"capacity"`
}

// Availability rules reported by explain=true, in this order (stage 3).
const (
	ruleCapacity  = "capacity"
	ruleNoOverlap = "no_overlap"
)

type ruleView struct {
	Rule  string `json:"rule"`
	Holds bool   `json:"holds"`
}

type explainView struct {
	TableID       string     `json:"table_id"`
	PolicyVersion int        `json:"policy_version"`
	Available     bool       `json:"available"`
	Rules         []ruleView `json:"rules"`
}

type slotView struct {
	StartsAtLocal     string         `json:"starts_at_local"`
	StartsAt          string         `json:"starts_at"`
	AvailableTableIDs []string       `json:"available_table_ids"`
	AvailableOptions  []optionView   `json:"available_options"`
	Explain           *[]explainView `json:"explain,omitempty"` // only with explain=true
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

// explainValue is the only accepted value of the optional explain parameter.
const explainValue = "true"

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
	values, explain := q["explain"]
	for _, v := range values {
		if v != explainValue {
			writeError(w, invalid("explain accepts only the value true"))
			return
		}
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
		Slots:        st.slotsFor(rest, date, party, explain),
	})
}

// slotsFor lists every grid slot of the day under the policy selected for that
// date, with the tables that can seat the party and are free for the whole
// reservation, then every declared pair that can. Caller holds the lock.
func (s *State) slotsFor(rest *Restaurant, date localDate, party int, explain bool) []slotView {
	terms := rest.termsFor(date)
	slots := []slotView{}
	weekday := weekdayOf(date)
	for _, h := range terms.Hours {
		if h.Weekday != weekday {
			continue
		}
		for minute := h.opensMin; minute+terms.DurationMinutes <= h.closesMin; minute += terms.SlotMinutes {
			lt := localTime{date, minute}
			start, ok := resolveLocal(rest.Location, lt)
			if !ok {
				continue // the slot falls in a spring-forward gap
			}
			end := start.Add(terms.duration())
			slot := slotView{StartsAtLocal: lt.String(), StartsAt: formatTime(start), AvailableTableIDs: []string{}, AvailableOptions: []optionView{}}
			explained := []explainView{}
			for _, t := range rest.Tables {
				fits := terms.Capacities[t.ID] >= party
				free := !s.tableBusy(rest, t.ID, start, end, nil)
				if fits && free {
					slot.AvailableTableIDs = append(slot.AvailableTableIDs, t.ID)
					slot.AvailableOptions = append(slot.AvailableOptions, optionView{[]string{t.ID}, terms.Capacities[t.ID]})
				}
				explained = append(explained, explainView{
					TableID: t.ID, PolicyVersion: terms.PolicyVersion, Available: fits && free,
					Rules: []ruleView{{ruleCapacity, fits}, {ruleNoOverlap, free}},
				})
			}
			for _, p := range rest.Combinable {
				capacity := terms.capacity(p)
				if capacity >= party && !s.setBusy(rest, p, start, end, nil) {
					slot.AvailableOptions = append(slot.AvailableOptions, optionView{[]string{p[0], p[1]}, capacity})
				}
			}
			if explain {
				slot.Explain = &explained
			}
			slots = append(slots, slot)
		}
	}
	return slots
}
