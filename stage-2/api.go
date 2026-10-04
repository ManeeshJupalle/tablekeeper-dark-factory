package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"regexp"
	"strconv"
)

// Error codes from the requirements (§5 and the per-endpoint tables).
const (
	codeMalformed        = "malformed_request"
	codeMissingKey       = "missing_idempotency_key"
	codeUnauthenticated  = "unauthenticated"
	codeNotFound         = "not_found"
	codeKeyReuse         = "idempotency_key_reuse"
	codeValidation       = "validation_failed"
	codeEmailTaken       = "email_taken"
	codeTableUnavailable = "table_unavailable"
	codeNotOnGrid        = "not_on_slot_grid"
	codeOutsideHours     = "outside_opening_hours"
	codeExceedsCapacity  = "party_exceeds_capacity"
	codeInvalidLocalTime = "invalid_local_time"
	codeCutoffPassed     = "cutoff_passed"
	codeCancelled        = "reservation_cancelled"
	codeMethodNotAllowed = "method_not_allowed"
	codePayloadTooLarge  = "payload_too_large"
	codeInternal         = "internal_error"
)

// Request body limits. Test-control bodies (reset, import) may carry a whole
// exported state; ordinary API bodies are tiny.
const (
	maxBodyBytes     = 1 << 20
	maxTestBodyBytes = 64 << 20
)

// apiError is a 4xx/5xx outcome; every one is rendered in the shared envelope.
type apiError struct {
	Status  int
	Code    string
	Message string
}

func (e *apiError) Error() string { return e.Code + ": " + e.Message }

func fail(status int, code, message string) *apiError {
	return &apiError{Status: status, Code: code, Message: message}
}

func invalid(message string) *apiError { return fail(http.StatusUnprocessableEntity, codeValidation, message) }

func malformed(message string) *apiError { return fail(http.StatusBadRequest, codeMalformed, message) }

func notFound(message string) *apiError { return fail(http.StatusNotFound, codeNotFound, message) }

func writeRaw(w http.ResponseWriter, status int, body []byte) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.Header().Set("Content-Length", strconv.Itoa(len(body)))
	w.WriteHeader(status)
	_, _ = w.Write(body)
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	body, err := json.Marshal(v)
	if err != nil {
		writeError(w, fail(http.StatusInternalServerError, codeInternal, "response encoding failed"))
		return
	}
	writeRaw(w, status, body)
}

func writeError(w http.ResponseWriter, e *apiError) {
	body, _ := json.Marshal(map[string]any{"error": map[string]string{"code": e.Code, "message": e.Message}})
	writeRaw(w, e.Status, body)
}

func writeNoContent(w http.ResponseWriter) { w.WriteHeader(http.StatusNoContent) }

// readBody drains the request body up to limit bytes.
func readBody(w http.ResponseWriter, r *http.Request, limit int64) ([]byte, *apiError) {
	r.Body = http.MaxBytesReader(w, r.Body, limit)
	body, err := io.ReadAll(r.Body)
	if err != nil {
		var tooLarge *http.MaxBytesError
		if errors.As(err, &tooLarge) {
			return nil, fail(http.StatusRequestEntityTooLarge, codePayloadTooLarge, "request body too large")
		}
		return nil, malformed("request body could not be read")
	}
	return body, nil
}

// parseJSONObject decodes a body that must be exactly one JSON object.
// Numbers are kept as json.Number so integer rules can be applied to the literal.
func parseJSONObject(body []byte) (map[string]any, *apiError) {
	dec := json.NewDecoder(bytes.NewReader(body))
	dec.UseNumber()
	var v any
	if err := dec.Decode(&v); err != nil {
		return nil, malformed("request body is not valid JSON")
	}
	var trailing any
	if err := dec.Decode(&trailing); err != io.EOF {
		return nil, malformed("request body has trailing content after the JSON object")
	}
	m, ok := v.(map[string]any)
	if !ok {
		return nil, malformed("request body must be a JSON object")
	}
	return m, nil
}

// canonicalJSON renders a decoded JSON value with sorted keys and no insignificant
// whitespace, so two bodies compare equal exactly when they are the same JSON value.
func canonicalJSON(v any) string {
	b, _ := json.Marshal(v)
	return string(b)
}

var integerLiteral = regexp.MustCompile(`^-?[0-9]+$`)

// jsonInt accepts only a JSON number written as a plain integer literal.
func jsonInt(v any) (int64, bool) {
	n, ok := v.(json.Number)
	if !ok || !integerLiteral.MatchString(n.String()) {
		return 0, false
	}
	i, err := strconv.ParseInt(n.String(), 10, 64)
	return i, err == nil
}

// stringField reports a string-typed field: present says whether the key exists,
// ok is false when it exists with a non-string value (including null).
func stringField(m map[string]any, key string) (value string, present bool, ok bool) {
	v, present := m[key]
	if !present {
		return "", false, true
	}
	s, ok := v.(string)
	return s, true, ok
}
