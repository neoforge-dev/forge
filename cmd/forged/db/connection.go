// Package db provides unified database connection management for FORGE v3.
// Supports both SQLite (development) and PostgreSQL (production).
package db

import (
	"context"
	"database/sql"
	"fmt"
	"os"
	"strconv"
	"time"

	_ "github.com/jackc/pgx/v5/stdlib"
	_ "github.com/mattn/go-sqlite3"
)

// DBType represents the database backend type.
type DBType string

const (
	SQLite     DBType = "sqlite"
	PostgreSQL DBType = "postgres"
)

// Config holds database configuration.
type Config struct {
	Type DBType

	// SQLite settings
	Path string

	// PostgreSQL settings
	Host     string
	Port     int
	User     string
	Password string
	Database string
	SSLMode  string

	// Connection pool settings
	MaxOpenConns    int
	MaxIdleConns    int
	ConnMaxLifetime time.Duration
	ConnMaxIdleTime time.Duration
}

// DefaultConfig returns default configuration from environment.
func DefaultConfig() *Config {
	dbType := DBType(os.Getenv("DB_TYPE"))
	if dbType == "" {
		dbType = SQLite
	}

	cfg := &Config{
		Type:            dbType,
		Path:            os.Getenv("DB_PATH"),
		Host:            os.Getenv("DB_HOST"),
		User:            os.Getenv("DB_USER"),
		Password:        os.Getenv("DB_PASSWORD"),
		Database:        os.Getenv("DB_NAME"),
		SSLMode:         os.Getenv("DB_SSLMODE"),
		MaxOpenConns:    getEnvInt("DB_MAX_OPEN_CONNS", 25),
		MaxIdleConns:    getEnvInt("DB_MAX_IDLE_CONNS", 5),
		ConnMaxLifetime: getEnvDuration("DB_CONN_MAX_LIFETIME", 5*time.Minute),
		ConnMaxIdleTime: getEnvDuration("DB_CONN_MAX_IDLE_TIME", 10*time.Minute),
	}

	if cfg.Port == 0 {
		cfg.Port = getEnvInt("DB_PORT", 5432)
	}

	if cfg.SSLMode == "" {
		cfg.SSLMode = "prefer"
	}

	return cfg
}

// ConnectionString returns the database-specific connection string.
func (c *Config) ConnectionString() string {
	switch c.Type {
	case PostgreSQL:
		return fmt.Sprintf(
			"host=%s port=%d user=%s password=%s dbname=%s sslmode=%s",
			c.Host, c.Port, c.User, c.Password, c.Database, c.SSLMode,
		)
	case SQLite:
		if c.Path == "" {
			c.Path = "./.forge/forge-v3.db"
		}
		return c.Path + "?_journal_mode=WAL&_busy_timeout=5000"
	default:
		return c.Path
	}
}

// DriverName returns the database driver name for sql.Open.
func (c *Config) DriverName() string {
	switch c.Type {
	case PostgreSQL:
		return "pgx"
	case SQLite:
		return "sqlite3"
	default:
		return "sqlite3"
	}
}

// Open creates a new database connection with pooling configured.
func Open(cfg *Config) (*sql.DB, error) {
	if cfg == nil {
		cfg = DefaultConfig()
	}

	db, err := sql.Open(cfg.DriverName(), cfg.ConnectionString())
	if err != nil {
		return nil, fmt.Errorf("open %s database: %w", cfg.Type, err)
	}

	// Configure connection pool
	db.SetMaxOpenConns(cfg.MaxOpenConns)
	db.SetMaxIdleConns(cfg.MaxIdleConns)
	db.SetConnMaxLifetime(cfg.ConnMaxLifetime)
	db.SetConnMaxIdleTime(cfg.ConnMaxIdleTime)

	// Verify connection
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	if err := db.PingContext(ctx); err != nil {
		db.Close()
		return nil, fmt.Errorf("ping %s database: %w", cfg.Type, err)
	}

	return db, nil
}

// OpenWithMigration creates a connection and runs migrations.
func OpenWithMigration(cfg *Config, migrateFn func(*sql.DB) error) (*sql.DB, error) {
	db, err := Open(cfg)
	if err != nil {
		return nil, err
	}

	if migrateFn != nil {
		if err := migrateFn(db); err != nil {
			db.Close()
			return nil, fmt.Errorf("migrate database: %w", err)
		}
	}

	return db, nil
}

// IsPostgreSQL returns true if the database is PostgreSQL.
func IsPostgreSQL(db *sql.DB) bool {
	var version string
	err := db.QueryRow("SELECT version()").Scan(&version)
	if err != nil {
		return false
	}
	return contains(version, "PostgreSQL")
}

// IsSQLite returns true if the database is SQLite.
func IsSQLite(db *sql.DB) bool {
	var version string
	err := db.QueryRow("SELECT sqlite_version()").Scan(&version)
	if err != nil {
		return false
	}
	return version != ""
}

// Dialect returns SQL dialect helpers for the database type.
func Dialect(db *sql.DB) *DialectHelper {
	if IsPostgreSQL(db) {
		return &DialectHelper{
			Placeholder:    pqPlaceholder,
			AutoIncrement:  "GENERATED ALWAYS AS IDENTITY",
			CurrentTime:    "NOW()",
			Boolean:        "BOOLEAN",
			Text:           "TEXT",
			JSON:           "JSONB",
			Timestamp:      "TIMESTAMPTZ",
			UpsertConflict: "ON CONFLICT (id) DO UPDATE SET",
		}
	}
	return &DialectHelper{
		Placeholder:    sqlitePlaceholder,
		AutoIncrement:  "INTEGER PRIMARY KEY AUTOINCREMENT",
		CurrentTime:    "datetime('now')",
		Boolean:        "INTEGER",
		Text:           "TEXT",
		JSON:           "TEXT",
		Timestamp:      "TEXT",
		UpsertConflict: "ON CONFLICT(id) DO UPDATE SET",
	}
}

// DialectHelper provides SQL dialect-specific strings.
type DialectHelper struct {
	Placeholder    func(index int) string
	AutoIncrement  string
	CurrentTime    string
	Boolean        string
	Text           string
	JSON           string
	Timestamp      string
	UpsertConflict string
}

func pqPlaceholder(index int) string {
	return fmt.Sprintf("$%d", index)
}

func sqlitePlaceholder(index int) string {
	return "?"
}

func contains(s, substr string) bool {
	return len(s) >= len(substr) && (s == substr || len(s) > 0 && containsAt(s, substr, 0))
}

func containsAt(s, substr string, start int) bool {
	for i := start; i <= len(s)-len(substr); i++ {
		if s[i:i+len(substr)] == substr {
			return true
		}
	}
	return false
}

func getEnvInt(key string, defaultVal int) int {
	if v := os.Getenv(key); v != "" {
		if i, err := strconv.Atoi(v); err == nil {
			return i
		}
	}
	return defaultVal
}

func getEnvDuration(key string, defaultVal time.Duration) time.Duration {
	if v := os.Getenv(key); v != "" {
		if d, err := time.ParseDuration(v); err == nil {
			return d
		}
	}
	return defaultVal
}
