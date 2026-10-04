package main

import (
	"encoding/json"
	"net/http"
)

// Dated booking policies (stage 3). A policy is published by a restaurant
// manager, is immutable, and governs every booking whose local start date is
// on or after its effective_from (see Restaurant.termsFor).

// Stated ranges for a published policy.
const (
	minPolicyMinutes  = 1
	maxPolicyMinutes  = 1440
	minPolicyCutoff   = 0
	maxPolicyCutoff   = 10080
	minPolicyCapacity = 1
	maxPolicyCapacity = 100
)

type termsView struct {
	PolicyVersion              int                `json:"policy_version"`
	SlotMinutes                int                `json:"slot_minutes"`
	ReservationDurationMinutes int                `json:"reservation_duration_minutes"`
	CancellationCutoffMinutes  int                `json:"cancellation_cutoff_minutes"`
	OpeningHours               []openingHoursView `json:"opening_hours"`
	Capacities                 map[string]int     `json:"capacities"`
}

func hoursView(hours []OpeningHours) []openingHoursView {
	out := []openingHoursView{}
	for _, h := range hours {
		out = append(out, openingHoursView{h.Weekday, h.Opens, h.Closes})
	}
	return out
}

func termsViewOf(t *Terms) termsView {
	caps := make(map[string]int, len(t.Capacities))
	for id, c := range t.Capacities {
		caps[id] = c
	}
	return termsView{
		PolicyVersion:              t.PolicyVersion,
		SlotMinutes:                t.SlotMinutes,
		ReservationDurationMinutes: t.DurationMinutes,
		CancellationCutoffMinutes:  t.CutoffMinutes,
		OpeningHours:               hoursView(t.Hours),
		Capacities:                 caps,
	}
}

// policyView is a published policy as supplied, plus its policy_version.
type policyView struct {
	EffectiveFrom              string             `json:"effective_from"`
	SlotMinutes                int                `json:"slot_minutes"`
	ReservationDurationMinutes int                `json:"reservation_duration_minutes"`
	CancellationCutoffMinutes  int                `json:"cancellation_cutoff_minutes"`
	OpeningHours               []openingHoursView `json:"opening_hours"`
	Capacities                 map[string]int     `json:"capacities"`
	PolicyVersion              int                `json:"policy_version"`
}

func policyViewOf(p *Policy) policyView {
	t := termsViewOf(p.Terms)
	return policyView{
		EffectiveFrom:              p.EffectiveFrom.String(),
		SlotMinutes:                t.SlotMinutes,
		ReservationDurationMinutes: t.ReservationDurationMinutes,
		CancellationCutoffMinutes:  t.CancellationCutoffMinutes,
		OpeningHours:               t.OpeningHours,
		Capacities:                 t.Capacities,
		PolicyVersion:              t.PolicyVersion,
	}
}

// policyInt reads a required integer field within [min, max]. A policy is
// validated as a whole: any missing, mistyped or out-of-range value is 422.
func policyInt(o obj, key string, min, max int64) (int, *apiError) {
	v, present := o[key]
	if !present {
		return 0, invalid(key + " is required")
	}
	n, ok := jsonInt(v)
	if !ok || n < min || n > max {
		return 0, invalid(key + " must be an integer in the stated range")
	}
	return int(n), nil
}

// parsePolicy validates a complete policy for a restaurant. Unknown fields are
// ignored; the version is assigned by the caller.
func parsePolicy(rest *Restaurant, o obj) (*Policy, *apiError) {
	raw, ok := o["effective_from"].(string)
	if !ok {
		return nil, invalid("effective_from must be a YYYY-MM-DD date")
	}
	eff, ok := parseLocalDate(raw)
	if !ok {
		return nil, invalid("effective_from must be a real YYYY-MM-DD date")
	}
	t := &Terms{}
	var e *apiError
	if t.SlotMinutes, e = policyInt(o, "slot_minutes", minPolicyMinutes, maxPolicyMinutes); e != nil {
		return nil, e
	}
	if t.DurationMinutes, e = policyInt(o, "reservation_duration_minutes", minPolicyMinutes, maxPolicyMinutes); e != nil {
		return nil, e
	}
	if t.CutoffMinutes, e = policyInt(o, "cancellation_cutoff_minutes", minPolicyCutoff, maxPolicyCutoff); e != nil {
		return nil, e
	}
	hours, ok := o["opening_hours"].([]any)
	if !ok {
		return nil, invalid("opening_hours must be an array")
	}
	seen := map[string]bool{}
	t.Hours = []OpeningHours{}
	for _, hv := range hours {
		h, e := parseOpeningHours(hv)
		if e != nil {
			return nil, e
		}
		if seen[h.Weekday] {
			return nil, invalid("opening_hours names " + h.Weekday + " twice")
		}
		seen[h.Weekday] = true
		t.Hours = append(t.Hours, h)
	}
	caps, ok := o["capacities"].(map[string]any)
	if !ok {
		return nil, invalid("capacities must be an object")
	}
	if len(caps) != len(rest.Tables) {
		return nil, invalid("capacities must name exactly the restaurant's tables")
	}
	t.Capacities = map[string]int{}
	for _, tbl := range rest.Tables {
		if _, present := caps[tbl.ID]; !present {
			return nil, invalid("capacities must name table " + tbl.ID)
		}
		c, e := policyInt(caps, tbl.ID, minPolicyCapacity, maxPolicyCapacity)
		if e != nil {
			return nil, e
		}
		t.Capacities[tbl.ID] = c
	}
	return &Policy{EffectiveFrom: eff, Terms: t}, nil
}

func policiesPath(restaurantID string) string { return "/restaurants/" + restaurantID + "/policies" }

func (a *app) publishPolicy(w http.ResponseWriter, r *http.Request, restaurantID string) {
	body, e := readBody(w, r, maxBodyBytes)
	if e != nil {
		writeError(w, e)
		return
	}
	a.store.mu.Lock()
	defer a.store.mu.Unlock()
	st := a.store.state
	m, e := parseJSONObject(body)
	if e != nil {
		writeError(w, e)
		return
	}
	user, e := authenticate(st, r)
	if e != nil {
		writeError(w, e)
		return
	}
	rest := st.restaurantsByID[restaurantID]
	if rest == nil {
		writeError(w, notFound("no such restaurant"))
		return
	}
	if !rest.isManager(user.ID) {
		writeError(w, fail(http.StatusForbidden, codeForbidden, "only the restaurant's managers may publish policies"))
		return
	}
	key, e := idempotencyKey(r)
	if e != nil {
		writeError(w, e)
		return
	}
	k := idemKey{user.ID, http.MethodPost, policiesPath(rest.ID), key}
	canonical := canonicalJSON(m)
	if replay(w, st, k, canonical) {
		return
	}
	p, e := parsePolicy(rest, m)
	if e != nil {
		writeError(w, e)
		return
	}
	p.Terms.PolicyVersion = len(rest.Policies) + 1
	rest.Policies = append(rest.Policies, p)
	rest.Revision++
	resp, _ := json.Marshal(policyViewOf(p))
	st.idem[k] = &IdemRecord{idemKey: k, Body: canonical, Status: http.StatusCreated, Response: resp}
	writeRaw(w, http.StatusCreated, resp)
}

func (a *app) listPolicies(w http.ResponseWriter, r *http.Request, restaurantID string) {
	a.store.mu.Lock()
	defer a.store.mu.Unlock()
	rest := a.store.state.restaurantsByID[restaurantID]
	if rest == nil {
		writeError(w, notFound("no such restaurant"))
		return
	}
	out := []policyView{}
	for _, p := range rest.Policies {
		out = append(out, policyViewOf(p))
	}
	writeJSON(w, http.StatusOK, map[string]any{"policies": out})
}
