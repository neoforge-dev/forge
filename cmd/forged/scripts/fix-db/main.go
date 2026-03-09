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

	_, err = db.Exec("ALTER TABLE approvals RENAME TO approvals_legacy")
	if err != nil {
		log.Printf("Warning: failed to rename approvals (maybe it doesn't exist?): %v", err)
	} else {
		fmt.Println("Renamed approvals to approvals_legacy")
	}

	_, err = db.Exec("ALTER TABLE context_artifacts RENAME TO context_artifacts_legacy")
	if err != nil {
		log.Printf("Warning: failed to rename context_artifacts: %v", err)
	} else {
		fmt.Println("Renamed context_artifacts to context_artifacts_legacy")
	}
}
