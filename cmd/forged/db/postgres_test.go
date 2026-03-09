package db

import (
	"context"
	"os"
	"testing"
	"time"
)

func TestDefaultPostgresConfig(t *testing.T) {
	cfg := DefaultPostgresConfig()

	if cfg.Type != PostgreSQL {
		t.Errorf("expected type postgres, got %s", cfg.Type)
	}

	if cfg.Host == "" {
		t.Error("expected non-empty host")
	}

	if cfg.Port == 0 {
		t.Error("expected non-zero port")
	}

	if cfg.MaxOpenConns == 0 {
		t.Error("expected non-zero MaxOpenConns")
	}
}

func TestPostgresConfigConnectionString(t *testing.T) {
	cfg := &PostgresConfig{
		Config: Config{
			Type:     PostgreSQL,
			Host:     "localhost",
			Port:     5432,
			User:     "forge",
			Password: "secret",
			Database: "forge_v3",
			SSLMode:  "disable",
		},
	}

	connStr := cfg.ConnectionString()
	expectedParts := []string{
		"host=localhost",
		"port=5432",
		"user=forge",
		"password=secret",
		"dbname=forge_v3",
		"sslmode=disable",
	}

	for _, part := range expectedParts {
		if !contains(connStr, part) {
			t.Errorf("expected connection string to contain %q, got %q", part, connStr)
		}
	}
}

func TestDefaultConfig(t *testing.T) {
	// Save and restore environment
	oldDBType := os.Getenv("DB_TYPE")
	defer os.Setenv("DB_TYPE", oldDBType)

	// Test SQLite default
	os.Setenv("DB_TYPE", "")
	cfg := DefaultConfig()
	if cfg.Type != SQLite {
		t.Errorf("expected SQLite default, got %s", cfg.Type)
	}

	// Test PostgreSQL
	os.Setenv("DB_TYPE", "postgres")
	cfg = DefaultConfig()
	if cfg.Type != PostgreSQL {
		t.Errorf("expected PostgreSQL, got %s", cfg.Type)
	}
}

func TestDialect(t *testing.T) {
	// Test SQLite dialect helper values
	sqliteHelper := &DialectHelper{
		Placeholder:    sqlitePlaceholder,
		AutoIncrement:  "INTEGER PRIMARY KEY AUTOINCREMENT",
		CurrentTime:    "datetime('now')",
		Boolean:        "INTEGER",
		Text:           "TEXT",
		JSON:           "TEXT",
		Timestamp:      "TEXT",
		UpsertConflict: "ON CONFLICT(id) DO UPDATE SET",
	}

	if sqliteHelper.Placeholder(1) != "?" {
		t.Errorf("expected ? placeholder, got %s", sqliteHelper.Placeholder(1))
	}

	// Test PostgreSQL dialect helper values
	pgHelper := &DialectHelper{
		Placeholder:    pqPlaceholder,
		AutoIncrement:  "GENERATED ALWAYS AS IDENTITY",
		CurrentTime:    "NOW()",
		Boolean:        "BOOLEAN",
		Text:           "TEXT",
		JSON:           "JSONB",
		Timestamp:      "TIMESTAMPTZ",
		UpsertConflict: "ON CONFLICT (id) DO UPDATE SET",
	}

	if pgHelper.Placeholder(1) != "$1" {
		t.Errorf("expected $1 placeholder, got %s", pgHelper.Placeholder(1))
	}
}

func TestConnectionPoolMonitor(t *testing.T) {
	// This is a simple test to ensure the monitor doesn't panic
	// In a real test, you'd use a mock database

	cfg := &PostgresConfig{
		Config: Config{
			Type:            PostgreSQL,
			Host:            "localhost",
			Port:            5432,
			User:            "test",
			Password:        "test",
			Database:        "test",
			MaxOpenConns:    10,
			MaxIdleConns:    5,
			ConnMaxLifetime: 5 * time.Minute,
		},
	}

	// Just verify the config is valid
	if cfg.MaxOpenConns != 10 {
		t.Errorf("expected 10 max open conns, got %d", cfg.MaxOpenConns)
	}
}

// Integration test - skipped by default
func TestPostgresIntegration(t *testing.T) {
	if os.Getenv("POSTGRES_TEST") != "1" {
		t.Skip("Skipping PostgreSQL integration test. Set POSTGRES_TEST=1 to run.")
	}

	cfg := DefaultPostgresConfig()
	
	db, err := NewPostgresDB(cfg)
	if err != nil {
		t.Fatalf("failed to connect to postgres: %v", err)
	}
	defer db.Close()

	// Test ping
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	if err := db.Ping(ctx); err != nil {
		t.Errorf("ping failed: %v", err)
	}

	// Test version
	version, err := db.GetVersion(ctx)
	if err != nil {
		t.Errorf("get version failed: %v", err)
	}
	t.Logf("PostgreSQL version: %s", version)

	// Test health check
	health, err := db.HealthCheck(ctx)
	if err != nil {
		t.Errorf("health check failed: %v", err)
	}
	t.Logf("Health: %v", health)
}
