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

	rows, err := db.Query("SELECT name FROM sqlite_master WHERE type='table'")
	if err != nil {
		log.Fatalf("failed to query tables: %v", err)
	}
	defer rows.Close()

	fmt.Println("Tables in DB:")
	for rows.Next() {
		var name string
		err := rows.Scan(&name)
		if err != nil {
			log.Fatalf("failed to scan row: %v", err)
		}
		fmt.Printf("- %s\n", name)
	}
}
