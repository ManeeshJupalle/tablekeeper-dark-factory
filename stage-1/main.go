// Tablekeeper stage 1: restaurant reservations over HTTP.
//
// The whole service runs in one process with an in-memory store guarded by a
// single mutex, so every check-and-write (table occupancy, idempotency claims,
// reset, import) is one atomic step. Nothing is fetched at run time; the IANA
// time zone database is compiled into the binary via time/tzdata.
package main

import (
	"log"
	"net/http"
	"os"
	"time"
	_ "time/tzdata"
)

const (
	defaultPort       = "8080"
	readHeaderTimeout = 10 * time.Second
	readTimeout       = 30 * time.Second
	writeTimeout      = 30 * time.Second
	idleTimeout       = 120 * time.Second
)

func main() {
	port := os.Getenv("PORT")
	if port == "" {
		port = defaultPort
	}
	srv := &http.Server{
		Addr:              "0.0.0.0:" + port,
		Handler:           newHandler(NewStore()),
		ReadHeaderTimeout: readHeaderTimeout,
		ReadTimeout:       readTimeout,
		WriteTimeout:      writeTimeout,
		IdleTimeout:       idleTimeout,
	}
	log.Printf("tablekeeper listening on %s", srv.Addr)
	log.Fatal(srv.ListenAndServe())
}
