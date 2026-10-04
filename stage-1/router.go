package main

import (
	"log"
	"net/http"
	"strings"
)

type app struct {
	store *Store
}

func newHandler(store *Store) http.Handler {
	a := &app{store: store}
	return recoverMiddleware(http.HandlerFunc(a.route))
}

// recoverMiddleware turns an unexpected panic into a JSON error instead of a
// dropped connection.
func recoverMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		defer func() {
			if rec := recover(); rec != nil {
				log.Printf("panic serving %s %s: %v", r.Method, r.URL.Path, rec)
				writeError(w, fail(http.StatusInternalServerError, codeInternal, "unexpected server error"))
			}
		}()
		next.ServeHTTP(w, r)
	})
}

type methodHandlers map[string]http.HandlerFunc

// dispatch runs the handler for the request method or answers 405.
func dispatch(w http.ResponseWriter, r *http.Request, handlers methodHandlers) {
	if h, ok := handlers[r.Method]; ok {
		h(w, r)
		return
	}
	allowed := make([]string, 0, len(handlers))
	for m := range handlers {
		allowed = append(allowed, m)
	}
	w.Header().Set("Allow", strings.Join(allowed, ", "))
	writeError(w, fail(http.StatusMethodNotAllowed, codeMethodNotAllowed, "method not allowed"))
}

func (a *app) route(w http.ResponseWriter, r *http.Request) {
	path := r.URL.Path
	if len(path) > 1 {
		path = strings.TrimSuffix(path, "/")
	}
	segments := strings.Split(strings.TrimPrefix(path, "/"), "/")

	switch {
	case path == "/health":
		dispatch(w, r, methodHandlers{http.MethodGet: a.health})
	case path == "/_test/reset":
		dispatch(w, r, methodHandlers{http.MethodPost: a.reset})
	case path == "/_test/export":
		dispatch(w, r, methodHandlers{http.MethodGet: a.export})
	case path == "/_test/import":
		dispatch(w, r, methodHandlers{http.MethodPost: a.importState})
	case path == "/auth/signup":
		dispatch(w, r, methodHandlers{http.MethodPost: a.signup})
	case path == "/auth/login":
		dispatch(w, r, methodHandlers{http.MethodPost: a.login})
	case path == "/restaurants":
		dispatch(w, r, methodHandlers{http.MethodGet: a.listRestaurants})
	case len(segments) == 2 && segments[0] == "restaurants":
		dispatch(w, r, methodHandlers{http.MethodGet: func(w http.ResponseWriter, r *http.Request) {
			a.getRestaurant(w, r, segments[1])
		}})
	case path == "/availability":
		dispatch(w, r, methodHandlers{http.MethodGet: a.availability})
	case path == pathReservations:
		dispatch(w, r, methodHandlers{http.MethodGet: a.listReservations, http.MethodPost: a.createReservation})
	case len(segments) == 2 && segments[0] == "reservations":
		dispatch(w, r, methodHandlers{
			http.MethodGet:   func(w http.ResponseWriter, r *http.Request) { a.getReservation(w, r, segments[1]) },
			http.MethodPatch: func(w http.ResponseWriter, r *http.Request) { a.patchReservation(w, r, segments[1]) },
		})
	case len(segments) == 3 && segments[0] == "reservations" && segments[2] == "cancel":
		dispatch(w, r, methodHandlers{http.MethodPost: func(w http.ResponseWriter, r *http.Request) {
			a.cancelReservation(w, r, segments[1])
		}})
	case path == pathMoves:
		dispatch(w, r, methodHandlers{http.MethodPost: a.moveReservations})
	default:
		writeError(w, notFound("no such route"))
	}
}

func (a *app) health(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}
