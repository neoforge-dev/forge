//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"database/sql"
	"embed"
	"fmt"
	"log"
	"os"
	"path"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"

	_ "github.com/jackc/pgx/v5/stdlib"
	_ "github.com/mattn/go-sqlite3"
)

//go:embed migrations/*.sql
var migrationsFS embed.FS

func getDefaultDBPath() string {
	forgeRoot := os.Getenv("FORGE_ROOT")
	if forgeRoot == "" {
		forgeRoot = "."
	}
	return filepath.Join(forgeRoot, ".forge/forge-v3.db")
}

var defaultDBPath = getDefaultDBPath()

type Migration struct {
	Version int
	Name    string
	UpSQL   string
	DownSQL string
}

// DBType represents the database backend type
type DBType string

const (
	SQLite     DBType = "sqlite"
	PostgreSQL DBType = "postgres"
)

// detectDBType determines if the database is SQLite or PostgreSQL
func detectDBType(db *sql.DB) DBType {
	// Try PostgreSQL first
	var version string
	err := db.QueryRow("SELECT version()").Scan(&version)
	if err == nil && strings.Contains(version, "PostgreSQL") {
		return PostgreSQL
	}
	return SQLite
}

// isPostgresMigration checks if a migration file is PostgreSQL-specific
func isPostgresMigration(filename string) bool {
	return strings.Contains(filename, "_postgres")
}

// isSQLiteMigration checks if a migration file is SQLite-specific
func isSQLiteMigration(filename string) bool {
	return strings.Contains(filename, "_sqlite")
}

// getMigrationsForDB returns the appropriate migration files for the database type
func getMigrationsForDB(dbType DBType) ([]Migration, error) {
	entries, err := migrationsFS.ReadDir("migrations")
	if err != nil {
		return nil, fmt.Errorf("read migrations dir: %w", err)
	}

	var migrations []Migration

	for _, e := range entries {
		if e.IsDir() {
			continue
		}
		name := e.Name()
		if !strings.HasSuffix(name, ".sql") {
			continue
		}

		// Filter by database type
		if dbType == PostgreSQL {
			// Skip SQLite-specific migrations
			if isSQLiteMigration(name) {
				continue
			}
		} else {
			// Skip PostgreSQL-specific migrations for SQLite
			if isPostgresMigration(name) {
				continue
			}
		}

		version, migName, err := parseMigrationFilename(name)
		if err != nil {
			return nil, err
		}

		data, err := migrationsFS.ReadFile(path.Join("migrations", name))
		if err != nil {
			return nil, fmt.Errorf("read migration %s: %w", name, err)
		}

		up, down := splitUpDown(string(data))

		// Convert SQL dialect if needed
		if dbType == PostgreSQL {
			up = convertToPostgresSQL(up)
			down = convertToPostgresSQL(down)
		}

		migrations = append(migrations, Migration{
			Version: version,
			Name:    migName,
			UpSQL:   up,
			DownSQL: down,
		})
	}

	sort.Slice(migrations, func(i, j int) bool {
		return migrations[i].Version < migrations[j].Version
	})

	return migrations, nil
}

// convertToPostgresSQL converts SQLite-specific SQL to PostgreSQL
func convertToPostgresSQL(sql string) string {
	if sql == "" {
		return ""
	}

	// Replace SQLite placeholders with PostgreSQL positional parameters
	// This is a simple conversion - complex queries may need manual adjustment
	result := sql

	// Replace datetime functions
	result = strings.ReplaceAll(result, "datetime('now')", "NOW()")
	result = strings.ReplaceAll(result, "CURRENT_TIMESTAMP", "NOW()")

	// Replace AUTOINCREMENT (SQLite specific) with GENERATED ALWAYS AS IDENTITY
	result = strings.ReplaceAll(result, "INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY")

	// Replace SQLite's TEXT for timestamps with TIMESTAMPTZ
	result = strings.ReplaceAll(result, "TEXT NOT NULL DEFAULT (datetime('now'))", "TIMESTAMPTZ NOT NULL DEFAULT NOW()")

	return result
}

func OpenDB(dbPath string) (*sql.DB, error) {
	// Check if using PostgreSQL
	if os.Getenv("DB_TYPE") == "postgres" {
		return OpenPostgresDB()
	}

	// Default to SQLite
	if dbPath == "" {
		dbPath = defaultDBPath
	}

	if err := os.MkdirAll(filepath.Dir(dbPath), 0o755); err != nil {
		return nil, fmt.Errorf("create db directory: %w", err)
	}

	conn, err := sql.Open("sqlite3", dbPath+"?_journal_mode=WAL&_busy_timeout=5000&parseTime=true")
	if err != nil {
		return nil, fmt.Errorf("open database: %w", err)
	}

	if err := conn.Ping(); err != nil {
		conn.Close()
		return nil, fmt.Errorf("ping database: %w", err)
	}

	return conn, nil
}

func OpenPostgresDB() (*sql.DB, error) {
	host := os.Getenv("DB_HOST")
	if host == "" {
		host = "localhost"
	}

	port := os.Getenv("DB_PORT")
	if port == "" {
		port = "5432"
	}

	user := os.Getenv("DB_USER")
	if user == "" {
		user = "forge"
	}

	password := os.Getenv("DB_PASSWORD")
	dbname := os.Getenv("DB_NAME")
	if dbname == "" {
		dbname = "forge_v3"
	}

	sslmode := os.Getenv("DB_SSLMODE")
	if sslmode == "" {
		sslmode = "prefer"
	}

	connStr := fmt.Sprintf("host=%s port=%s user=%s password=%s dbname=%s sslmode=%s",
		host, port, user, password, dbname, sslmode)

	conn, err := sql.Open("pgx", connStr)
	if err != nil {
		return nil, fmt.Errorf("open postgres database: %w", err)
	}

	// Configure connection pool
	maxConns := 25
	if v := os.Getenv("DB_MAX_OPEN_CONNS"); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			maxConns = n
		}
	}
	conn.SetMaxOpenConns(maxConns)
	conn.SetMaxIdleConns(10)
	conn.SetConnMaxLifetime(5 * time.Minute)

	if err := conn.Ping(); err != nil {
		conn.Close()
		return nil, fmt.Errorf("ping postgres database: %w", err)
	}

	log.Println("connected to PostgreSQL database")
	return conn, nil
}

func MigrateUp(db *sql.DB) error {
	dbType := detectDBType(db)
	log.Printf("detected database type: %s", dbType)

	if err := ensureMigrationsTable(db, dbType); err != nil {
		return err
	}

	migrations, err := getMigrationsForDB(dbType)
	if err != nil {
		return err
	}

	if len(migrations) == 0 {
		log.Println("no migrations found, skipping")
		return nil
	}

	applied, err := getAppliedMigrations(db)
	if err != nil {
		return err
	}

	for _, m := range migrations {
		if applied[m.Version] {
			continue
		}

		log.Printf("applying migration %03d_%s\n", m.Version, m.Name)

		tx, err := db.Begin()
		if err != nil {
			return fmt.Errorf("begin tx: %w", err)
		}

		if strings.TrimSpace(m.UpSQL) != "" {
			if _, err := tx.Exec(m.UpSQL); err != nil {
				tx.Rollback()
				return fmt.Errorf("apply migration %d: %w", m.Version, err)
			}
		}

		// Use dialect-specific placeholder
		var insertSQL string
		if dbType == PostgreSQL {
			insertSQL = `INSERT INTO applied_migrations (version, name, applied_at) VALUES ($1, $2, $3)`
		} else {
			insertSQL = `INSERT INTO applied_migrations (version, name, applied_at) VALUES (?, ?, ?)`
		}

		if _, err := tx.Exec(
			insertSQL,
			m.Version,
			m.Name,
			time.Now().UTC().Format(time.RFC3339),
		); err != nil {
			tx.Rollback()
			return fmt.Errorf("record applied migration %d: %w", m.Version, err)
		}

		if err := tx.Commit(); err != nil {
			return fmt.Errorf("commit migration %d: %w", m.Version, err)
		}
	}

	return nil
}

// MigrateDown rolls back the last N applied migrations.
func MigrateDown(db *sql.DB, steps int) error {
	if steps <= 0 {
		return nil
	}

	dbType := detectDBType(db)

	if err := ensureMigrationsTable(db, dbType); err != nil {
		return err
	}

	migrations, err := getMigrationsForDB(dbType)
	if err != nil {
		return err
	}
	if len(migrations) == 0 {
		return nil
	}

	migrationByVersion := make(map[int]Migration, len(migrations))
	for _, m := range migrations {
		migrationByVersion[m.Version] = m
	}

	rows, err := db.Query(`SELECT version, name FROM applied_migrations ORDER BY version DESC`)
	if err != nil {
		return fmt.Errorf("list applied migrations: %w", err)
	}
	defer rows.Close()

	remaining := steps

	for rows.Next() && remaining > 0 {
		var version int
		var name string
		if err := rows.Scan(&version, &name); err != nil {
			return fmt.Errorf("scan applied migration: %w", err)
		}

		m, ok := migrationByVersion[version]
		if !ok {
			return fmt.Errorf("no migration definition for version %d", version)
		}
		if strings.TrimSpace(m.DownSQL) == "" {
			return fmt.Errorf("no down migration defined for %d_%s", m.Version, m.Name)
		}

		log.Printf("rolling back migration %03d_%s\n", m.Version, m.Name)

		tx, err := db.Begin()
		if err != nil {
			return fmt.Errorf("begin tx: %w", err)
		}

		if _, err := tx.Exec(m.DownSQL); err != nil {
			tx.Rollback()
			return fmt.Errorf("rollback migration %d: %w", m.Version, err)
		}

		var deleteSQL string
		if dbType == PostgreSQL {
			deleteSQL = `DELETE FROM applied_migrations WHERE version = $1`
		} else {
			deleteSQL = `DELETE FROM applied_migrations WHERE version = ?`
		}

		if _, err := tx.Exec(deleteSQL, version); err != nil {
			tx.Rollback()
			return fmt.Errorf("delete applied migration %d: %w", m.Version, err)
		}

		if err := tx.Commit(); err != nil {
			return fmt.Errorf("commit rollback %d: %w", m.Version, err)
		}

		remaining--
	}

	if err := rows.Err(); err != nil {
		return fmt.Errorf("iterate applied migrations: %w", err)
	}

	return nil
}

func ensureMigrationsTable(db *sql.DB, dbType DBType) error {
	var err error

	if dbType == PostgreSQL {
		_, err = db.Exec(`
CREATE TABLE IF NOT EXISTS applied_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL
);
`)
	} else {
		_, err = db.Exec(`
CREATE TABLE IF NOT EXISTS applied_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL
);
`)
	}

	if err != nil {
		return fmt.Errorf("create applied_migrations table: %w", err)
	}
	return nil
}

// columnExists checks if a column exists in a SQLite table
func columnExists(db *sql.DB, table, column string) (bool, error) {
	var count int
	err := db.QueryRow(
		"SELECT COUNT(*) FROM pragma_table_info(?) WHERE name = ?",
		table, column,
	).Scan(&count)
	if err != nil {
		return false, fmt.Errorf("check column existence: %w", err)
	}
	return count > 0, nil
}

// indexExists checks if an index exists in SQLite
func indexExists(db *sql.DB, indexName string) (bool, error) {
	var count int
	err := db.QueryRow(
		"SELECT COUNT(*) FROM sqlite_master WHERE type = 'index' AND name = ?",
		indexName,
	).Scan(&count)
	if err != nil {
		return false, fmt.Errorf("check index existence: %w", err)
	}
	return count > 0, nil
}

func getAppliedMigrations(db *sql.DB) (map[int]bool, error) {
	rows, err := db.Query(`SELECT version FROM applied_migrations`)
	if err != nil {
		return nil, fmt.Errorf("select applied migrations: %w", err)
	}
	defer rows.Close()

	applied := make(map[int]bool)
	for rows.Next() {
		var v int
		if err := rows.Scan(&v); err != nil {
			return nil, fmt.Errorf("scan applied migration: %w", err)
		}
		applied[v] = true
	}

	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate applied migrations: %w", err)
	}

	return applied, nil
}

func loadMigrations() ([]Migration, error) {
	entries, err := migrationsFS.ReadDir("migrations")
	if err != nil {
		return nil, fmt.Errorf("read migrations dir: %w", err)
	}

	var migrations []Migration

	for _, e := range entries {
		if e.IsDir() {
			continue
		}
		name := e.Name()
		if !strings.HasSuffix(name, ".sql") {
			continue
		}

		version, migName, err := parseMigrationFilename(name)
		if err != nil {
			return nil, err
		}

		data, err := migrationsFS.ReadFile(path.Join("migrations", name))
		if err != nil {
			return nil, fmt.Errorf("read migration %s: %w", name, err)
		}

		up, down := splitUpDown(string(data))

		migrations = append(migrations, Migration{
			Version: version,
			Name:    migName,
			UpSQL:   up,
			DownSQL: down,
		})
	}

	sort.Slice(migrations, func(i, j int) bool {
		return migrations[i].Version < migrations[j].Version
	})

	return migrations, nil
}

func parseMigrationFilename(filename string) (int, string, error) {
	base := strings.TrimSuffix(filename, ".sql")
	parts := strings.SplitN(base, "_", 2)
	if len(parts) == 0 {
		return 0, "", fmt.Errorf("invalid migration filename: %s", filename)
	}

	version, err := strconv.Atoi(parts[0])
	if err != nil {
		return 0, "", fmt.Errorf("invalid migration version in filename %s: %w", filename, err)
	}

	name := ""
	if len(parts) > 1 {
		name = parts[1]
	}

	return version, name, nil
}

func splitUpDown(sqlText string) (string, string) {
	const separator = "-- +migrate Down"

	parts := strings.SplitN(sqlText, separator, 2)
	up := strings.TrimSpace(parts[0])

	if len(parts) == 2 {
		down := strings.TrimSpace(parts[1])
		return up, down
	}

	return up, ""
}
