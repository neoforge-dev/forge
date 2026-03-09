// Package db provides PostgreSQL-specific database operations for FORGE v3.
package db

import (
	"context"
	"database/sql"
	"fmt"
	"log"
	"os"
	"strings"
	"time"

	_ "github.com/jackc/pgx/v5/stdlib"
)

// PostgresDB wraps PostgreSQL connection with FORGE-specific operations.
type PostgresDB struct {
	DB     *sql.DB
	Config *PostgresConfig
}

// PostgresConfig extends Config with replication settings.
type PostgresConfig struct {
	Config

	// Replication settings
	ReplicationHost     string
	ReplicationPort     int
	ReplicationUser     string
	ReplicationPassword string
	ReplicationDatabase string

	// Read replica support
	ReadReplicas []ReplicaConfig
}

// ReplicaConfig defines a read replica connection.
type ReplicaConfig struct {
	Host     string
	Port     int
	Weight   int // Load balancing weight
	Priority int // Failover priority (lower = higher priority)
}

// NewPostgresDB creates a new PostgreSQL connection with full configuration.
func NewPostgresDB(cfg *PostgresConfig) (*PostgresDB, error) {
	if cfg == nil {
		cfg = &PostgresConfig{
			Config: *DefaultConfig(),
		}
		cfg.Type = PostgreSQL
	}

	db, err := Open(&cfg.Config)
	if err != nil {
		return nil, err
	}

	return &PostgresDB{
		DB:     db,
		Config: cfg,
	}, nil
}

// Close closes the database connection.
func (p *PostgresDB) Close() error {
	if p.DB != nil {
		return p.DB.Close()
	}
	return nil
}

// Ping verifies the database connection.
func (p *PostgresDB) Ping(ctx context.Context) error {
	return p.DB.PingContext(ctx)
}

// Stats returns connection pool statistics.
func (p *PostgresDB) Stats() sql.DBStats {
	return p.DB.Stats()
}

// LogStats logs current connection pool statistics.
func (p *PostgresDB) LogStats() {
	stats := p.Stats()
	log.Printf("[postgres] pool stats: open=%d in_use=%d idle=%d wait_count=%d wait_duration=%s",
		stats.OpenConnections,
		stats.InUse,
		stats.Idle,
		stats.WaitCount,
		stats.WaitDuration,
	)
}

// EnableReplication configures logical replication for the database.
func (p *PostgresDB) EnableReplication(ctx context.Context, publicationName string) error {
	// Check if publication exists
	var exists bool
	err := p.DB.QueryRowContext(ctx,
		"SELECT EXISTS (SELECT 1 FROM pg_publication WHERE pubname = $1)",
		publicationName,
	).Scan(&exists)
	if err != nil {
		return fmt.Errorf("check publication: %w", err)
	}

	if exists {
		log.Printf("[postgres] publication %s already exists", publicationName)
		return nil
	}

	// Create publication for all tables
	_, err = p.DB.ExecContext(ctx,
		fmt.Sprintf("CREATE PUBLICATION %s FOR ALL TABLES", publicationName),
	)
	if err != nil {
		return fmt.Errorf("create publication: %w", err)
	}

	log.Printf("[postgres] created publication %s", publicationName)
	return nil
}

// CreateReplicationSlot creates a logical replication slot.
func (p *PostgresDB) CreateReplicationSlot(ctx context.Context, slotName string) (string, error) {
	var snapshotName string
	query := fmt.Sprintf("SELECT pg_create_logical_replication_slot('%s', 'pgoutput')", slotName)
	err := p.DB.QueryRowContext(ctx, query).Scan(&snapshotName)
	if err != nil {
		if strings.Contains(err.Error(), "already exists") {
			return "", fmt.Errorf("slot %s already exists", slotName)
		}
		return "", fmt.Errorf("create replication slot: %w", err)
	}
	return snapshotName, nil
}

// DropReplicationSlot removes a replication slot.
func (p *PostgresDB) DropReplicationSlot(ctx context.Context, slotName string) error {
	_, err := p.DB.ExecContext(ctx,
		fmt.Sprintf("SELECT pg_drop_replication_slot('%s')", slotName),
	)
	if err != nil {
		return fmt.Errorf("drop replication slot: %w", err)
	}
	return nil
}

// WaitForReplicationLag waits until replication lag is below the threshold.
func (p *PostgresDB) WaitForReplicationLag(ctx context.Context, maxLag time.Duration) error {
	for {
		var lagSeconds float64
		err := p.DB.QueryRowContext(ctx, `
			SELECT EXTRACT(EPOCH FROM (now() - pg_last_xact_replay_timestamp()))
			WHERE pg_last_xact_replay_timestamp() IS NOT NULL
		`).Scan(&lagSeconds)
		if err != nil {
			return fmt.Errorf("check replication lag: %w", err)
		}

		lag := time.Duration(lagSeconds * float64(time.Second))
		if lag <= maxLag {
			return nil
		}

		log.Printf("[postgres] replication lag %v > %v, waiting...", lag, maxLag)

		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(time.Second):
			continue
		}
	}
}

// Transaction executes a function within a database transaction.
func (p *PostgresDB) Transaction(ctx context.Context, fn func(*sql.Tx) error) error {
	tx, err := p.DB.BeginTx(ctx, nil)
	if err != nil {
		return fmt.Errorf("begin transaction: %w", err)
	}
	defer tx.Rollback()

	if err := fn(tx); err != nil {
		return err
	}

	if err := tx.Commit(); err != nil {
		return fmt.Errorf("commit transaction: %w", err)
	}

	return nil
}

// AdvisoryLock acquires a PostgreSQL advisory lock.
func (p *PostgresDB) AdvisoryLock(ctx context.Context, key int64) (bool, error) {
	var acquired bool
	err := p.DB.QueryRowContext(ctx, "SELECT pg_try_advisory_lock($1)", key).Scan(&acquired)
	if err != nil {
		return false, fmt.Errorf("acquire advisory lock: %w", err)
	}
	return acquired, nil
}

// AdvisoryUnlock releases a PostgreSQL advisory lock.
func (p *PostgresDB) AdvisoryUnlock(ctx context.Context, key int64) (bool, error) {
	var released bool
	err := p.DB.QueryRowContext(ctx, "SELECT pg_advisory_unlock($1)", key).Scan(&released)
	if err != nil {
		return false, fmt.Errorf("release advisory lock: %w", err)
	}
	return released, nil
}

// Listen sets up a NOTIFY/LISTEN channel.
func (p *PostgresDB) Listen(ctx context.Context, channel string) (*Listener, error) {
	// Create a new connection dedicated to listening
	listenerDB, err := Open(&p.Config.Config)
	if err != nil {
		return nil, fmt.Errorf("create listener connection: %w", err)
	}

	_, err = listenerDB.ExecContext(ctx, fmt.Sprintf("LISTEN %s", channel))
	if err != nil {
		listenerDB.Close()
		return nil, fmt.Errorf("listen on channel %s: %w", channel, err)
	}

	return &Listener{
		db:      listenerDB,
		channel: channel,
		msgs:    make(chan string, 100),
		ctx:     ctx,
	}, nil
}

// Listener handles PostgreSQL NOTIFY messages.
type Listener struct {
	db      *sql.DB
	channel string
	msgs    chan string
	ctx     context.Context
	cancel  context.CancelFunc
}

// Close stops the listener.
func (l *Listener) Close() error {
	if l.cancel != nil {
		l.cancel()
	}
	close(l.msgs)
	return l.db.Close()
}

// Channel returns the message channel.
func (l *Listener) Channel() <-chan string {
	return l.msgs
}

// Notify sends a notification to a channel.
func (p *PostgresDB) Notify(ctx context.Context, channel, payload string) error {
	_, err := p.DB.ExecContext(ctx,
		fmt.Sprintf("NOTIFY %s, '%s'", channel, payload),
	)
	if err != nil {
		return fmt.Errorf("notify channel %s: %w", channel, err)
	}
	return nil
}

// GetVersion returns the PostgreSQL server version.
func (p *PostgresDB) GetVersion(ctx context.Context) (string, error) {
	var version string
	err := p.DB.QueryRowContext(ctx, "SELECT version()").Scan(&version)
	if err != nil {
		return "", fmt.Errorf("get version: %w", err)
	}
	return version, nil
}

// TableSizes returns the size of each table in the database.
func (p *PostgresDB) TableSizes(ctx context.Context) (map[string]string, error) {
	rows, err := p.DB.QueryContext(ctx, `
		SELECT schemaname || '.' || relname AS table_name,
			pg_size_pretty(pg_total_relation_size(relid)) AS size
		FROM pg_catalog.pg_statio_user_tables
		ORDER BY pg_total_relation_size(relid) DESC
	`)
	if err != nil {
		return nil, fmt.Errorf("query table sizes: %w", err)
	}
	defer rows.Close()

	sizes := make(map[string]string)
	for rows.Next() {
		var table, size string
		if err := rows.Scan(&table, &size); err != nil {
			return nil, err
		}
		sizes[table] = size
	}

	return sizes, rows.Err()
}

// Vacuum runs VACUUM ANALYZE on a table.
func (p *PostgresDB) Vacuum(ctx context.Context, tableName string) error {
	// VACUUM cannot run inside a transaction block
	_, err := p.DB.ExecContext(ctx, fmt.Sprintf("VACUUM ANALYZE %s", tableName))
	if err != nil {
		return fmt.Errorf("vacuum %s: %w", tableName, err)
	}
	return nil
}

// ConnectionPoolMonitor periodically logs connection pool stats.
type ConnectionPoolMonitor struct {
	db       *PostgresDB
	interval time.Duration
	stop     chan struct{}
}

// StartConnectionPoolMonitor begins monitoring the connection pool.
func StartConnectionPoolMonitor(db *PostgresDB, interval time.Duration) *ConnectionPoolMonitor {
	if interval <= 0 {
		interval = 30 * time.Second
	}

	monitor := &ConnectionPoolMonitor{
		db:       db,
		interval: interval,
		stop:     make(chan struct{}),
	}

	go func() {
		ticker := time.NewTicker(interval)
		defer ticker.Stop()

		for {
			select {
			case <-ticker.C:
				db.LogStats()
			case <-monitor.stop:
				return
			}
		}
	}()

	return monitor
}

// Stop stops the monitor.
func (m *ConnectionPoolMonitor) Stop() {
	close(m.stop)
}

// DefaultPostgresConfig creates a production-ready PostgreSQL configuration.
func DefaultPostgresConfig() *PostgresConfig {
	return &PostgresConfig{
		Config: Config{
			Type:            PostgreSQL,
			Host:            getEnvOrDefault("DB_HOST", "localhost"),
			Port:            getEnvInt("DB_PORT", 5432),
			User:            getEnvOrDefault("DB_USER", "forge"),
			Password:        getEnvOrDefault("DB_PASSWORD", ""),
			Database:        getEnvOrDefault("DB_NAME", "forge_v3"),
			SSLMode:         getEnvOrDefault("DB_SSLMODE", "prefer"),
			MaxOpenConns:    25,
			MaxIdleConns:    10,
			ConnMaxLifetime: 5 * time.Minute,
			ConnMaxIdleTime: 10 * time.Minute,
		},
		ReplicationHost:     getEnvOrDefault("DB_REPL_HOST", ""),
		ReplicationPort:     getEnvInt("DB_REPL_PORT", 5432),
		ReplicationUser:     getEnvOrDefault("DB_REPL_USER", ""),
		ReplicationPassword: getEnvOrDefault("DB_REPL_PASSWORD", ""),
		ReplicationDatabase: getEnvOrDefault("DB_REPL_NAME", ""),
	}
}

func getEnvOrDefault(key, defaultVal string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return defaultVal
}

// Reconnect attempts to reconnect to the database with exponential backoff.
func (p *PostgresDB) Reconnect(ctx context.Context, maxRetries int) error {
	backoff := time.Second

	for i := 0; i < maxRetries; i++ {
		// Close existing connection
		if p.DB != nil {
			p.DB.Close()
		}

		// Try to open new connection
		db, err := Open(&p.Config.Config)
		if err != nil {
			log.Printf("[postgres] reconnect attempt %d/%d failed: %v", i+1, maxRetries, err)
			select {
			case <-ctx.Done():
				return ctx.Err()
			case <-time.After(backoff):
				backoff *= 2
				if backoff > 30*time.Second {
					backoff = 30 * time.Second
				}
				continue
			}
		}

		p.DB = db
		log.Printf("[postgres] reconnected successfully")
		return nil
	}

	return fmt.Errorf("failed to reconnect after %d attempts", maxRetries)
}

// HealthCheck performs a comprehensive health check.
func (p *PostgresDB) HealthCheck(ctx context.Context) (map[string]interface{}, error) {
	health := make(map[string]interface{})

	// Basic connectivity
	if err := p.Ping(ctx); err != nil {
		health["status"] = "unhealthy"
		health["error"] = err.Error()
		return health, err
	}

	health["status"] = "healthy"
	health["pool_stats"] = p.Stats()

	// Version
	version, err := p.GetVersion(ctx)
	if err == nil {
		health["version"] = version
	}

	// Table sizes
	sizes, err := p.TableSizes(ctx)
	if err == nil {
		health["table_sizes"] = sizes
	}

	return health, nil
}
