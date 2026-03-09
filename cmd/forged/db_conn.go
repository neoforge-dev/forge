package main

import (
	"database/sql"
	"sync/atomic"
)

// dbConn is a process-wide database handle used by HTTP/WebSocket handlers.
// The sql.DB type itself is safe for concurrent use, but swapping the pointer
// must be synchronized to satisfy the race detector (tests may replace it).
var dbConn atomic.Pointer[sql.DB]

func getDBConn() *sql.DB {
	return dbConn.Load()
}

func setDBConn(db *sql.DB) {
	dbConn.Store(db)
}

