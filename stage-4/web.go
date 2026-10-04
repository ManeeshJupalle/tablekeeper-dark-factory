package main

import (
	"embed"
	"html"
	"net/http"
	"path"
	"strings"
)

// The browser product (stage 2). Every screen and asset is compiled into the
// binary, so the container serves the whole UI with no outbound network.

//go:embed web
var webFiles embed.FS

// restaurantOptionsMarker is replaced in index.html with the current restaurant
// list, so the search screen is usable before any script runs.
const restaurantOptionsMarker = "<!--RESTAURANT_OPTIONS-->"

var staticContentTypes = map[string]string{
	".css": "text/css; charset=utf-8",
	".js":  "text/javascript; charset=utf-8",
	".svg": "image/svg+xml",
}

func (a *app) screen(w http.ResponseWriter, r *http.Request, file string) {
	data, err := webFiles.ReadFile("web/" + file)
	if err != nil {
		writeError(w, notFound("no such screen"))
		return
	}
	page := string(data)
	if strings.Contains(page, restaurantOptionsMarker) {
		page = strings.Replace(page, restaurantOptionsMarker, a.restaurantOptions(), 1)
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write([]byte(page))
}

func (a *app) restaurantOptions() string {
	a.store.mu.Lock()
	defer a.store.mu.Unlock()
	var b strings.Builder
	for _, rest := range a.store.state.restaurants {
		b.WriteString(`<option value="`)
		b.WriteString(html.EscapeString(rest.ID))
		b.WriteString(`">`)
		b.WriteString(html.EscapeString(rest.Name))
		b.WriteString("</option>")
	}
	return b.String()
}

func (a *app) static(w http.ResponseWriter, r *http.Request, name string) {
	contentType, known := staticContentTypes[path.Ext(name)]
	data, err := webFiles.ReadFile("web/static/" + name)
	if err != nil || !known {
		writeError(w, notFound("no such asset"))
		return
	}
	w.Header().Set("Content-Type", contentType)
	w.Header().Set("Cache-Control", "no-cache")
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write(data)
}
