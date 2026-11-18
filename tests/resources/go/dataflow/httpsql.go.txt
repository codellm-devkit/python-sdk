// Package httpsql is the L4 "done" fixture (spec §8): an HTTP handler's
// request reaches database/sql's DB.Query through an interface call, and a
// second handler takes the same route through strconv.Atoi first.
package httpsql

import (
	"database/sql"
	"net/http"
	"strconv"
)

// Repo is the storage interface the handlers call; sqlRepo is its one
// implementation, so the interface calls bind sqlRepo's methods by VTA.
type Repo interface {
	Find(id string) (*sql.Rows, error)
	SanitizedFind(id string) (*sql.Rows, error)
	Count(user string) (*sql.Rows, error)
}

type sqlRepo struct{ db *sql.DB }

// NewRepo returns the database-backed Repo.
func NewRepo(db *sql.DB) Repo { return &sqlRepo{db: db} }

// Find builds the query from id: the sink.
func (s *sqlRepo) Find(id string) (*sql.Rows, error) {
	return s.db.Query("SELECT name FROM users WHERE id = " + id)
}

// SanitizedFind parses id as a number first and queries with the number.
func (s *sqlRepo) SanitizedFind(id string) (*sql.Rows, error) {
	n, err := strconv.Atoi(id)
	if err != nil {
		return nil, err
	}
	return s.db.Query("SELECT name FROM users WHERE id = " + strconv.Itoa(n))
}

// Count counts a user's rows; only Audit calls it.
func (s *sqlRepo) Count(user string) (*sql.Rows, error) {
	return s.db.Query("SELECT count(*) FROM events WHERE user = " + user)
}

// Handler reads the id query parameter and looks it up.
func Handler(repo Repo) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		id := r.URL.Query().Get("id")
		rows, err := repo.Find(id)
		if err != nil {
			http.Error(w, "lookup failed", http.StatusInternalServerError)
			return
		}
		rows.Close()
	}
}

// SanitizedHandler is Handler through SanitizedFind.
func SanitizedHandler(repo Repo) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		id := r.URL.Query().Get("id")
		rows, err := repo.SanitizedFind(id)
		if err != nil {
			http.Error(w, "lookup failed", http.StatusInternalServerError)
			return
		}
		rows.Close()
	}
}

// Health answers without touching the database.
func Health(w http.ResponseWriter, r *http.Request) {
	w.WriteHeader(http.StatusOK)
}

// Audit is an entry point apart from Routes: it builds its own Repo inline
// (no call), so it shares no host variable and no in-project callee with the
// handlers; its only in-project callee is sqlRepo.Count.
func Audit(db *sql.DB, user string) error {
	var repo Repo = &sqlRepo{db: db}
	rows, err := repo.Count(user)
	if err != nil {
		return err
	}
	return rows.Close()
}

// Routes wires the handlers to a database-backed Repo: without it no
// concrete type reaches the handlers' repo, and VTA binds repo.Find to
// nothing but the interface method.
func Routes(mux *http.ServeMux, db *sql.DB) {
	repo := NewRepo(db)
	mux.Handle("/user", Handler(repo))
	mux.Handle("/user/sanitized", SanitizedHandler(repo))
	mux.HandleFunc("/health", Health)
}
