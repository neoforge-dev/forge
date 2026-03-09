//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bufio"
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"log"
	"net"
	"net/http"
	"strconv"
	"sync"
	"sync/atomic"
	"time"

	"github.com/google/uuid"
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

var (
	// Task metrics
	tasksCreated = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "forge_tasks_total",
		Help: "Total number of tasks created",
	}, []string{"state"})

	taskDuration = promauto.NewHistogram(prometheus.HistogramOpts{
		Name:    "forge_tasks_duration_seconds",
		Help:    "Task execution duration in seconds",
		Buckets: prometheus.DefBuckets,
	})

	leasesActive = promauto.NewGauge(prometheus.GaugeOpts{
		Name: "forge_leases_active",
		Help: "Number of active task leases",
	})

	queueDepth = promauto.NewGauge(prometheus.GaugeOpts{
		Name: "forge_queue_depth",
		Help: "Number of pending tasks in queue",
	})

	// HTTP metrics
	httpRequestsTotal = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "forge_http_requests_total",
		Help: "Total number of HTTP requests",
	}, []string{"method", "endpoint", "status"})

	httpRequestDuration = promauto.NewHistogramVec(prometheus.HistogramOpts{
		Name:    "forge_http_request_duration_seconds",
		Help:    "HTTP request latency in seconds",
		Buckets: prometheus.DefBuckets,
	}, []string{"method", "endpoint"})

	// Connection metrics
	activeConnections = promauto.NewGauge(prometheus.GaugeOpts{
		Name: "forge_active_connections",
		Help: "Current WebSocket connections",
	})

	heartbeatsReceived = promauto.NewCounter(prometheus.CounterOpts{
		Name: "forge_heartbeats_received",
		Help: "Total heartbeats received",
	})
)

// Legacy compatibility struct (preserving structure but using Prometheus backend)
type Metrics struct {
	mu sync.RWMutex

	// Keep these for internal TUI and JSON stats if needed
	RequestsTotal      int64
	TasksCreated       int64
	TasksCompleted     int64
	TasksFailed        int64
	HeartbeatsReceived int64
	TasksEnqueued      int64

	// Ring buffer for response times (preserved for TUI)
	responseTimes    [1000]time.Duration
	responseTimesIdx int64
	responseTimesCnt int
}

var metrics = &Metrics{}
var metricsStopCh chan struct{}

// InitMetrics initializes the metrics counters
func InitMetrics() {
	if metricsStopCh != nil {
		return // already initialized
	}
	metricsStopCh = make(chan struct{})
	// Callbacks for dynamic gauges
	go func() {
		ticker := time.NewTicker(5 * time.Second)
		defer ticker.Stop()
		for {
			select {
			case <-metricsStopCh:
				return
			case <-ticker.C:
				if getQueueDepthFunc != nil {
					queueDepth.Set(float64(getQueueDepthFunc()))
				}
				if getActiveLeasesFunc != nil {
					leasesActive.Set(float64(getActiveLeasesFunc()))
				}
			}
		}
	}()
}

// StopMetrics stops the metrics goroutines
func StopMetrics() {
	if metricsStopCh != nil {
		close(metricsStopCh)
		metricsStopCh = nil
	}
}

// RecordRequest records an HTTP request and duration.
func (m *Metrics) RecordRequest(method, path string, status int, duration time.Duration) {
	atomic.AddInt64(&m.RequestsTotal, 1)

	// Update Prometheus
	httpRequestsTotal.WithLabelValues(method, path, strconv.Itoa(status)).Inc()
	httpRequestDuration.WithLabelValues(method, path).Observe(duration.Seconds())

	// Per-path metrics (protected by mutex) for legacy support
	m.mu.Lock()
	defer m.mu.Unlock()

	idx := atomic.AddInt64(&m.responseTimesIdx, 1) - 1
	m.responseTimes[idx%1000] = duration
	if m.responseTimesCnt < 1000 {
		m.responseTimesCnt++
	}
}

// RecordTaskCreated increments task created counter
func (m *Metrics) RecordTaskCreated(taskType string, priority int) {
	atomic.AddInt64(&metrics.TasksCreated, 1)
	tasksCreated.WithLabelValues("created").Inc()
}

// RecordTaskCompleted increments task completed counter
func (m *Metrics) RecordTaskCompleted() {
	atomic.AddInt64(&metrics.TasksCompleted, 1)
	tasksCreated.WithLabelValues("completed").Inc()
}

// RecordTaskFailed increments task failed counter
func (m *Metrics) RecordTaskFailed() {
	atomic.AddInt64(&metrics.TasksFailed, 1)
	tasksCreated.WithLabelValues("failed").Inc()
}

// RecordTaskState records a task state change
func (m *Metrics) RecordTaskState(state string, delta int64) {
	if delta > 0 {
		tasksCreated.WithLabelValues(state).Add(float64(delta))
	}
}

// RecordTaskDuration records task execution duration
func (m *Metrics) RecordTaskDuration(duration time.Duration) {
	taskDuration.Observe(duration.Seconds())
}

// RecordAgentTaskCompleted increments per-agent task completion counter.
func (m *Metrics) RecordAgentTaskCompleted(agentID string, duration time.Duration) {
	m.RecordTaskCompleted()
	m.RecordTaskDuration(duration)
}

// RecordAgentTaskFailed increments per-agent task failure counter.
func (m *Metrics) RecordAgentTaskFailed(agentID string, duration time.Duration) {
	m.RecordTaskFailed()
	m.RecordTaskDuration(duration)
}

// RecordTaskEnqueued increments task enqueued counter
func (m *Metrics) RecordTaskEnqueued() {
	atomic.AddInt64(&m.TasksEnqueued, 1)
	tasksCreated.WithLabelValues("queued").Inc()
}

// RecordHeartbeat increments heartbeat counter
func (m *Metrics) RecordHeartbeat() {
	atomic.AddInt64(&m.HeartbeatsReceived, 1)
	heartbeatsReceived.Inc()
}

// computeMetricsRollups performs periodic aggregation of telemetry and pattern run data (ADR-27).
func computeMetricsRollups(ctx context.Context, db *sql.DB) error {
	log.Println("[Metrics] Computing rollups (1m, 5m)...")

	// 1. 1-minute rolling average tokens/sec per agent (from agent_telemetry if available,
	//    or pattern_runs if that's where tokens are tracked)
	// ADR-27 mentions agent_telemetry, but the schema shows tokens_used in pattern_runs.
	// We'll compute both where possible.

	// 1m Tokens per second per agent (from pattern_runs)
	rows, err := db.QueryContext(ctx, `
		SELECT agent_id, 
		       SUM(tokens_used) / 60.0 as tokens_per_sec
		FROM pattern_runs
		WHERE started_at > datetime('now', '-1 minute')
		GROUP BY agent_id
	`)
	if err != nil {
		log.Printf("[Metrics] Error querying 1m tokens: %v", err)
	} else {
		defer rows.Close()
		for rows.Next() {
			var agentID string
			var tokensPerSec float64
			if err := rows.Scan(&agentID, &tokensPerSec); err == nil {
				labels, _ := json.Marshal(map[string]string{"agent": agentID})
				_, _ = db.ExecContext(ctx, `
					INSERT INTO metrics (metric_name, value, labels, period)
					VALUES (?, ?, ?, ?)
				`, "agent_tokens_per_sec", tokensPerSec, string(labels), "1m")
			}
		}
	}

	// 5-minute error rate (from pattern_runs)
	var errorRate float64
	err = db.QueryRowContext(ctx, `
		SELECT COALESCE(COUNT(CASE WHEN success = 0 THEN 1 END) * 1.0 / NULLIF(COUNT(*), 0), 0)
		FROM pattern_runs
		WHERE started_at > datetime('now', '-5 minutes')
	`).Scan(&errorRate)
	if err != nil {
		log.Printf("[Metrics] Error querying 5m error rate: %v", err)
	} else {
		_, _ = db.ExecContext(ctx, `
			INSERT INTO metrics (metric_name, value, labels, period)
			VALUES (?, ?, ?, ?)
		`, "fleet_error_rate", errorRate, "{}", "5m")
	}

	// 1-minute task throughput
	var taskCount int
	err = db.QueryRowContext(ctx, `
		SELECT COUNT(*) FROM tasks 
		WHERE updated_at > datetime('now', '-1 minute') AND status = 'completed'
	`).Scan(&taskCount)
	if err == nil {
		throughput := float64(taskCount) / 60.0
		_, _ = db.ExecContext(ctx, `
			INSERT INTO metrics (metric_name, value, labels, period)
			VALUES (?, ?, ?, ?)
		`, "task_throughput_per_sec", throughput, "{}", "1m")
	}

	return nil
}

// SetActiveConnections sets the current active connection count
func (m *Metrics) SetActiveConnections(count int64) {
	activeConnections.Set(float64(count))
}

// RecordPatrolExecution records a patrol execution start
func RecordPatrolExecution(db *sql.DB, patrolID string) {
	if db == nil {
		return
	}
	id := uuid.New().String()
	_, err := db.ExecContext(context.Background(),
		`INSERT INTO patrol_executions (id, patrol_id, started_at, status) VALUES (?, ?, datetime('now'), 'running')`,
		id, patrolID)
	if err != nil {
		log.Printf("[Metrics] RecordPatrolExecution error: %v", err)
	}
}

// RecordPatrolError records a patrol error
func RecordPatrolError(db *sql.DB, patrolID string, errMsg string) {
	if db == nil {
		return
	}
	_, err := db.ExecContext(context.Background(),
		`UPDATE patrol_executions SET status='error', error=?, completed_at=datetime('now')
		 WHERE id = (
		   SELECT id FROM patrol_executions
		   WHERE patrol_id=? AND status='running'
		   ORDER BY started_at DESC LIMIT 1
		 )`,
		errMsg, patrolID)
	if err != nil {
		log.Printf("[Metrics] RecordPatrolError error: %v", err)
	}
}

// RecordPatrolSuccess records a successful patrol completion
func RecordPatrolSuccess(db *sql.DB, patrolID string) {
	if db == nil {
		return
	}
	_, err := db.ExecContext(context.Background(),
		`UPDATE patrol_executions SET status='completed', completed_at=datetime('now')
		 WHERE id = (
		   SELECT id FROM patrol_executions
		   WHERE patrol_id=? AND status='running'
		   ORDER BY started_at DESC LIMIT 1
		 )`,
		patrolID)
	if err != nil {
		log.Printf("[Metrics] RecordPatrolSuccess error: %v", err)
	}
}

// GetPatrolMetrics returns current patrol metrics (Legacy shim)
func GetPatrolMetrics() map[string]interface{} {
	return map[string]interface{}{
		"executions": make(map[string]int64),
		"errors":     make(map[string]int64),
		"last_run":   make(map[string]string),
	}
}

// MetricsHandler returns Prometheus-style metrics
func MetricsHandler(w http.ResponseWriter, r *http.Request) {
	promhttp.Handler().ServeHTTP(w, r)
}

// DebugHandler returns debug information
func DebugHandler(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")

	debug := map[string]interface{}{
		"timestamp":           time.Now().Format(time.RFC3339),
		"uptime":              time.Since(startTime).String(),
		"requests_total":      atomic.LoadInt64(&metrics.RequestsTotal),
		"tasks_completed":     atomic.LoadInt64(&metrics.TasksCompleted),
		"tasks_failed":        atomic.LoadInt64(&metrics.TasksFailed),
		"heartbeats_received": atomic.LoadInt64(&metrics.HeartbeatsReceived),
	}

	json.NewEncoder(w).Encode(debug)
}

// DetailedHealthHandler returns detailed health status
func DetailedHealthHandler(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")

	health := map[string]interface{}{
		"status":    "ok",
		"timestamp": time.Now().Format(time.RFC3339),
	}

	json.NewEncoder(w).Encode(health)
}

// Logging middleware with structured logs
func LoggingMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()

		// Wrap response writer to capture status
		wrapped := &responseWriter{ResponseWriter: w, statusCode: 200}

		next.ServeHTTP(wrapped, r)

		duration := time.Since(start)
		metrics.RecordRequest(r.Method, r.URL.Path, wrapped.statusCode, duration)

		// Structured log
		log.Printf("request method=%s path=%s status=%d duration=%s",
			r.Method, r.URL.Path, wrapped.statusCode, duration)
	})
}

type responseWriter struct {
	http.ResponseWriter
	statusCode int
}

func (rw *responseWriter) WriteHeader(code int) {
	rw.statusCode = code
	rw.ResponseWriter.WriteHeader(code)
}

func (rw *responseWriter) Write(b []byte) (int, error) {
	if rw.statusCode == 0 {
		rw.statusCode = 200
	}
	return rw.ResponseWriter.Write(b)
}

func (rw *responseWriter) Hijack() (net.Conn, *bufio.ReadWriter, error) {
	if hj, ok := rw.ResponseWriter.(http.Hijacker); ok {
		return hj.Hijack()
	}
	return nil, nil, fmt.Errorf("http.Hijacker not implemented")
}

var startTime = time.Now()

// GetStartTime returns the server start time
func GetStartTime() time.Time {
	return startTime
}

// Callbacks for dynamic metrics (set from main.go)
var getQueueDepthFunc func() int64
var getActiveLeasesFunc func() int64

// SetQueueDepthFunc sets the callback for getting queue depth
func SetQueueDepthFunc(fn func() int64) {
	getQueueDepthFunc = fn
}

// SetActiveLeasesFunc sets the callback for getting active lease count
func SetActiveLeasesFunc(fn func() int64) {
	getActiveLeasesFunc = fn
}
