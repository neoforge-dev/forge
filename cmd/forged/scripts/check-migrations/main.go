package main

import (
	"database/sql"
	"fmt"
	"log"
	"os"

	_ "github.com/mattn/go-sqlite3"
)

func main() {
	dbPath := "../../.forge/forge-v3.db"
	if len(os.Args) > 1 {
		dbPath = os.Args[1]
	}
	db, err := sql.Open("sqlite3", dbPath)
	if err != nil {
		log.Fatalf("failed to open database: %v", err)
	}
	defer db.Close()

	rows, err := db.Query("SELECT version, name, applied_at FROM applied_migrations")
	if err != nil {
		log.Fatalf("failed to query migrations: %v", err)
	}
	defer rows.Close()

	fmt.Println("Applied migrations:")
	for rows.Next() {
		var version int
		var name, appliedAt string
		err := rows.Scan(&version, &name, &appliedAt)
		if err != nil {
			log.Fatalf("failed to scan row: %v", err)
		}
		fmt.Printf("- %d: %s (%s)\n", version, name, appliedAt)
	}
}
