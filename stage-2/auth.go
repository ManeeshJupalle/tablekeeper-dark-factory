package main

import (
	"crypto/sha256"
	"encoding/base64"
	"net/http"
	"strings"
	"unicode"
	"unicode/utf8"

	"golang.org/x/crypto/bcrypt"
)

const minPasswordLength = 8

// hashPassword stores passwords with bcrypt. The password is first reduced with
// SHA-256 so that bcrypt's 72-byte input limit never rejects a long password.
func hashPassword(password string) (string, error) {
	sum := sha256.Sum256([]byte(password))
	pre := base64.StdEncoding.EncodeToString(sum[:])
	h, err := bcrypt.GenerateFromPassword([]byte(pre), bcrypt.DefaultCost)
	return string(h), err
}

func checkPassword(hash, password string) bool {
	sum := sha256.Sum256([]byte(password))
	pre := base64.StdEncoding.EncodeToString(sum[:])
	return bcrypt.CompareHashAndPassword([]byte(hash), []byte(pre)) == nil
}

// validEmail requires the local@domain form: one @ with non-empty parts and no
// whitespace or control characters.
func validEmail(s string) bool {
	at := strings.IndexByte(s, '@')
	if at <= 0 || at != strings.LastIndexByte(s, '@') || at == len(s)-1 {
		return false
	}
	for _, r := range s {
		if unicode.IsSpace(r) || unicode.IsControl(r) {
			return false
		}
	}
	return true
}

type authResponse struct {
	UserID      string `json:"user_id"`
	DisplayName string `json:"display_name"`
	Token       string `json:"token"`
}

type credentials struct {
	email       string
	password    string
	displayName string
}

// parseCredentials applies the shared type rules: wrong JSON type is 400, a
// missing required field is 422.
func parseCredentials(m map[string]any, withDisplayName bool) (credentials, *apiError) {
	var c credentials
	var present, ok bool
	if c.email, present, ok = stringField(m, "email"); !ok {
		return c, malformed("email must be a string")
	} else if !present {
		return c, invalid("email is required")
	}
	if c.password, present, ok = stringField(m, "password"); !ok {
		return c, malformed("password must be a string")
	} else if !present {
		return c, invalid("password is required")
	}
	if withDisplayName {
		if c.displayName, _, ok = stringField(m, "display_name"); !ok {
			return c, malformed("display_name must be a string")
		}
	}
	return c, nil
}

func (a *app) signup(w http.ResponseWriter, r *http.Request) {
	body, e := readBody(w, r, maxBodyBytes)
	if e != nil {
		writeError(w, e)
		return
	}
	m, e := parseJSONObject(body)
	if e != nil {
		writeError(w, e)
		return
	}
	c, e := parseCredentials(m, true)
	if e != nil {
		writeError(w, e)
		return
	}
	if !validEmail(c.email) {
		writeError(w, invalid("email must have the form local@domain"))
		return
	}
	if utf8.RuneCountInString(c.password) < minPasswordLength {
		writeError(w, invalid("password must be at least 8 characters"))
		return
	}
	a.store.mu.Lock()
	taken := a.store.state.usersByEmail[emailKey(c.email)] != nil
	a.store.mu.Unlock()
	if taken {
		writeError(w, fail(http.StatusConflict, codeEmailTaken, "email already registered"))
		return
	}
	// Hashing is slow by design, so it happens outside the lock; the uniqueness
	// check is repeated under the lock before the account is written.
	hash, err := hashPassword(c.password)
	if err != nil {
		writeError(w, fail(http.StatusInternalServerError, codeInternal, "password hashing failed"))
		return
	}
	a.store.mu.Lock()
	defer a.store.mu.Unlock()
	st := a.store.state
	if st.usersByEmail[emailKey(c.email)] != nil {
		writeError(w, fail(http.StatusConflict, codeEmailTaken, "email already registered"))
		return
	}
	u := &User{ID: st.newUserID(), Email: c.email, DisplayName: c.displayName, PasswordHash: hash}
	st.addUser(u)
	tok := st.newToken()
	st.tokens[tok] = u.ID
	writeJSON(w, http.StatusCreated, authResponse{UserID: u.ID, DisplayName: u.DisplayName, Token: tok})
}

func (a *app) login(w http.ResponseWriter, r *http.Request) {
	body, e := readBody(w, r, maxBodyBytes)
	if e != nil {
		writeError(w, e)
		return
	}
	m, e := parseJSONObject(body)
	if e != nil {
		writeError(w, e)
		return
	}
	c, e := parseCredentials(m, false)
	if e != nil {
		writeError(w, e)
		return
	}
	unauthenticated := fail(http.StatusUnauthorized, codeUnauthenticated, "wrong email or password")

	a.store.mu.Lock()
	u := a.store.state.usersByEmail[emailKey(c.email)]
	var userID, hash string
	if u != nil {
		userID, hash = u.ID, u.PasswordHash
	}
	a.store.mu.Unlock()
	if u == nil || !checkPassword(hash, c.password) {
		writeError(w, unauthenticated)
		return
	}

	a.store.mu.Lock()
	defer a.store.mu.Unlock()
	st := a.store.state
	// The state may have been replaced while the password was being checked.
	u = st.usersByID[userID]
	if u == nil || u.PasswordHash != hash {
		writeError(w, unauthenticated)
		return
	}
	tok := st.newToken()
	st.tokens[tok] = u.ID
	writeJSON(w, http.StatusOK, authResponse{UserID: u.ID, DisplayName: u.DisplayName, Token: tok})
}

// authenticate resolves the bearer token. Caller must hold the store lock.
func authenticate(st *State, r *http.Request) (*User, *apiError) {
	parts := strings.Fields(r.Header.Get("Authorization"))
	if len(parts) != 2 || !strings.EqualFold(parts[0], "Bearer") {
		return nil, fail(http.StatusUnauthorized, codeUnauthenticated, "bearer token required")
	}
	uid, ok := st.tokens[parts[1]]
	if !ok {
		return nil, fail(http.StatusUnauthorized, codeUnauthenticated, "unknown token")
	}
	u := st.usersByID[uid]
	if u == nil {
		return nil, fail(http.StatusUnauthorized, codeUnauthenticated, "unknown token")
	}
	return u, nil
}
