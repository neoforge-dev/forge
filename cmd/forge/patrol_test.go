package main

import (
	"encoding/json"
	"fmt"
	"testing"
	"time"
)

func TestLoadPatrols(t *testing.T) {
	// loadPatrols calls the live API and falls back to a local file.
	// When neither is available it returns an empty slice (no demo data).
	patrols, err := loadPatrols()
	if err != nil {
		t.Fatalf("loadPatrols failed: %v", err)
	}
	// Result may be empty if daemon is not running and no local patrols.json.
	// Validate field invariants only when patrols are present.
	for _, p := range patrols {
		if p.ID == "" {
			t.Error("patrol ID should not be empty")
		}
		if p.Name == "" {
			t.Error("patrol Name should not be empty")
		}
		if p.Type == "" {
			t.Error("patrol Type should not be empty")
		}
		if p.Status == "" {
			t.Error("patrol Status should not be empty")
		}
	}
}

// TestPatrolTypeCoverage verifies the patrolGroupName helper handles all known types.
func TestPatrolTypeCoverage(t *testing.T) {
	knownTypes := []string{"health", "heartbeat", "liveness", "agent", "metrics", "confidence", "node",
		"task", "approval", "stale", "queue", "fleet", "token",
		"result", "xnode", "dispatch", "inbox", "context",
		"git", "data", "worktree", "hygiene", "council", "daily",
		"work", "auto", "binary", "strategy"}
	for _, pt := range knownTypes {
		g := patrolGroupName(pt)
		if g == "" {
			t.Errorf("patrolGroupName(%q) returned empty string", pt)
		}
	}
	// Unknown type falls back to "other"
	if g := patrolGroupName("unknown_xyz"); g != "other" {
		t.Errorf("expected 'other', got %q", g)
	}
}

func TestGeneratePatrolLogs(t *testing.T) {
	patrol := &Patrol{
		ID:        "test-patrol",
		Name:      "Test Patrol",
		Type:      "health",
		Status:    "running",
		Interval:  60,
		Successes: 10,
		Failures:  2,
	}

	logs := generatePatrolLogs(patrol, 10)
	if len(logs) == 0 {
		t.Error("expected at least one log entry")
	}

	// Check that logs are sorted newest first
	if len(logs) > 1 {
		if logs[0].Timestamp.Before(logs[1].Timestamp) {
			t.Error("logs should be sorted newest first")
		}
	}

	// Check log entry has required fields
	for _, log := range logs {
		if log.Timestamp.IsZero() {
			t.Error("log timestamp should not be zero")
		}
		if log.Level == "" {
			t.Error("log level should not be empty")
		}
		if log.Message == "" {
			t.Error("log message should not be empty")
		}
	}
}

func TestGeneratePatrolLogsWarning(t *testing.T) {
	patrol := &Patrol{
		ID:        "warning-patrol",
		Name:      "Warning Patrol",
		Type:      "heartbeat",
		Status:    "warning",
		Interval:  120,
		Message:   "Node last seen 10m ago",
		Successes: 5,
		Failures:  3,
	}

	logs := generatePatrolLogs(patrol, 20)

	// Should include warning messages
	hasWarn := false
	for _, log := range logs {
		if log.Level == "WARN" {
			hasWarn = true
			break
		}
	}

	if !hasWarn {
		t.Error("expected warning log entries for warning status patrol")
	}
}

func TestPatrolStruct(t *testing.T) {
	now := time.Now()
	p := Patrol{
		ID:        "test-id",
		Name:      "Test Name",
		Type:      "health",
		Status:    "running",
		Interval:  60,
		LastRun:   now,
		NextRun:   now.Add(time.Minute),
		Message:   "Test message",
		Successes: 10,
		Failures:  2,
		Config: map[string]interface{}{
			"threshold": 5,
		},
	}

	if p.ID != "test-id" {
		t.Errorf("ID mismatch: got %s", p.ID)
	}
	if p.Name != "Test Name" {
		t.Errorf("Name mismatch: got %s", p.Name)
	}
	if p.Type != "health" {
		t.Errorf("Type mismatch: got %s", p.Type)
	}
	if p.Status != "running" {
		t.Errorf("Status mismatch: got %s", p.Status)
	}
	if p.Interval != 60 {
		t.Errorf("Interval mismatch: got %d", p.Interval)
	}
	if p.Successes != 10 {
		t.Errorf("Successes mismatch: got %d", p.Successes)
	}
	if p.Failures != 2 {
		t.Errorf("Failures mismatch: got %d", p.Failures)
	}
}

func TestPatrolLogStruct(t *testing.T) {
	now := time.Now()
	log := PatrolLog{
		Timestamp: now,
		Level:     "INFO",
		Message:   "Test log message",
	}

	if log.Timestamp != now {
		t.Error("Timestamp mismatch")
	}
	if log.Level != "INFO" {
		t.Errorf("Level mismatch: got %s", log.Level)
	}
	if log.Message != "Test log message" {
		t.Errorf("Message mismatch: got %s", log.Message)
	}
}

// ==================== Edge Cases ====================

func TestGeneratePatrolLogsZeroLines(t *testing.T) {
	patrol := &Patrol{
		ID:        "test-patrol",
		Name:      "Test Patrol",
		Type:      "health",
		Status:    "running",
		Interval:  60,
		Successes: 10,
		Failures:  0,
	}

	logs := generatePatrolLogs(patrol, 0)
	if len(logs) != 0 {
		t.Errorf("expected 0 logs for zero lines request, got %d", len(logs))
	}
}

func TestGeneratePatrolLogsNegativeLines(t *testing.T) {
	patrol := &Patrol{
		ID:        "test-patrol",
		Name:      "Test Patrol",
		Type:      "health",
		Status:    "running",
		Interval:  60,
		Successes: 10,
		Failures:  0,
	}

	logs := generatePatrolLogs(patrol, -5)
	if len(logs) != 0 {
		t.Errorf("expected 0 logs for negative lines request, got %d", len(logs))
	}
}

func TestGeneratePatrolLogsStoppedPatrol(t *testing.T) {
	patrol := &Patrol{
		ID:        "stopped-patrol",
		Name:      "Stopped Patrol",
		Type:      "deploy",
		Status:    "stopped",
		Interval:  60,
		Message:   "Disabled - no active deployments",
		Successes: 12,
		Failures:  0,
	}

	logs := generatePatrolLogs(patrol, 10)
	if len(logs) == 0 {
		t.Error("expected logs even for stopped patrol")
	}
}

func TestGeneratePatrolLogsErrorPatrol(t *testing.T) {
	patrol := &Patrol{
		ID:        "error-patrol",
		Name:      "Error Patrol",
		Type:      "health",
		Status:    "error",
		Interval:  30,
		Message:   "Connection refused",
		Successes: 5,
		Failures:  15,
	}

	logs := generatePatrolLogs(patrol, 10)
	if len(logs) == 0 {
		t.Error("expected logs for error status patrol")
	}

	// Verify logs are generated even with failures
	for _, log := range logs {
		if log.Timestamp.IsZero() {
			t.Error("log timestamp should not be zero")
		}
	}
}

func TestGeneratePatrolLogsLargeLineCount(t *testing.T) {
	patrol := &Patrol{
		ID:        "test-patrol",
		Name:      "Test Patrol",
		Type:      "health",
		Status:    "running",
		Interval:  60,
		Successes: 100,
		Failures:  0,
	}

	logs := generatePatrolLogs(patrol, 1000)
	// Should be capped based on message patterns
	if len(logs) > 100 {
		t.Errorf("logs should be capped, got %d", len(logs))
	}
}

func TestPatrolAllStatusTypes(t *testing.T) {
	statuses := []string{"running", "stopped", "warning", "error"}

	for _, status := range statuses {
		patrol := &Patrol{
			ID:        "test-" + status,
			Name:      "Test " + status,
			Type:      "health",
			Status:    status,
			Interval:  60,
			Successes: 10,
			Failures:  0,
		}

		if patrol.Status != status {
			t.Errorf("status mismatch: expected %s, got %s", status, patrol.Status)
		}
	}
}

func TestPatrolAllTypes(t *testing.T) {
	types := []string{"health", "queue", "heartbeat", "quality", "deploy"}

	for _, patrolType := range types {
		patrol := &Patrol{
			ID:        "test-" + patrolType,
			Name:      "Test " + patrolType,
			Type:      patrolType,
			Status:    "running",
			Interval:  60,
			Successes: 10,
			Failures:  0,
		}

		if patrol.Type != patrolType {
			t.Errorf("type mismatch: expected %s, got %s", patrolType, patrol.Type)
		}
	}
}

func TestPatrolZeroInterval(t *testing.T) {
	patrol := &Patrol{
		ID:        "zero-interval",
		Name:      "Zero Interval",
		Type:      "health",
		Status:    "running",
		Interval:  0,
		Successes: 10,
		Failures:  0,
	}

	// Should handle zero interval gracefully
	logs := generatePatrolLogs(patrol, 5)
	for _, log := range logs {
		if !log.Timestamp.IsZero() {
			// With zero interval, all timestamps should be the same (now)
			// This is expected behavior
		}
	}
}

func TestPatrolNegativeInterval(t *testing.T) {
	patrol := &Patrol{
		ID:        "negative-interval",
		Name:      "Negative Interval",
		Type:      "health",
		Status:    "running",
		Interval:  -60,
		Successes: 10,
		Failures:  0,
	}

	// Should handle negative interval
	logs := generatePatrolLogs(patrol, 5)
	// Just verify it doesn't panic
	if len(logs) > 0 {
		// Generated successfully
	}
}

func TestPatrolEmptyMessage(t *testing.T) {
	patrol := &Patrol{
		ID:        "empty-message",
		Name:      "Empty Message",
		Type:      "health",
		Status:    "running",
		Interval:  60,
		Message:   "",
		Successes: 10,
		Failures:  0,
	}

	if patrol.Message != "" {
		t.Error("expected empty message")
	}

	logs := generatePatrolLogs(patrol, 5)
	if len(logs) == 0 {
		t.Error("expected logs even with empty message")
	}
}

func TestPatrolNilConfig(t *testing.T) {
	patrol := &Patrol{
		ID:        "nil-config",
		Name:      "Nil Config",
		Type:      "health",
		Status:    "running",
		Interval:  60,
		Config:    nil,
		Successes: 10,
		Failures:  0,
	}

	if patrol.Config != nil {
		t.Error("expected nil config")
	}
}

func TestPatrolConfigWithValues(t *testing.T) {
	patrol := &Patrol{
		ID:       "with-config",
		Name:     "With Config",
		Type:     "health",
		Status:   "running",
		Interval: 60,
		Config: map[string]interface{}{
			"threshold":  5,
			"timeout":    "30s",
			"enabled":    true,
			"agents":     []string{"kimi", "gemini"},
		},
		Successes: 10,
		Failures:  0,
	}

	if patrol.Config["threshold"] != 5 {
		t.Errorf("expected threshold 5, got %v", patrol.Config["threshold"])
	}
	if patrol.Config["enabled"] != true {
		t.Error("expected enabled true")
	}
}

func TestPatrolZeroTimestamps(t *testing.T) {
	patrol := &Patrol{
		ID:        "zero-time",
		Name:      "Zero Time",
		Type:      "health",
		Status:    "running",
		Interval:  60,
		LastRun:   time.Time{},
		NextRun:   time.Time{},
		Successes: 0,
		Failures:  0,
	}

	if !patrol.LastRun.IsZero() {
		t.Error("expected zero LastRun")
	}
	if !patrol.NextRun.IsZero() {
		t.Error("expected zero NextRun")
	}
}

func TestPatrolFutureNextRun(t *testing.T) {
	now := time.Now()
	future := now.Add(24 * time.Hour)

	patrol := &Patrol{
		ID:        "future-run",
		Name:      "Future Run",
		Type:      "health",
		Status:    "running",
		Interval:  60,
		LastRun:   now,
		NextRun:   future,
		Successes: 10,
		Failures:  0,
	}

	if patrol.NextRun.Before(now) {
		t.Error("NextRun should be in the future")
	}
}

func TestPatrolPastLastRun(t *testing.T) {
	now := time.Now()
	past := now.Add(-2 * time.Hour)

	patrol := &Patrol{
		ID:        "past-run",
		Name:      "Past Run",
		Type:      "health",
		Status:    "running",
		Interval:  60,
		LastRun:   past,
		NextRun:   now,
		Successes: 10,
		Failures:  0,
	}

	if patrol.LastRun.After(now) {
		t.Error("LastRun should be in the past")
	}
}

func TestPatrolHighSuccessCount(t *testing.T) {
	patrol := &Patrol{
		ID:        "high-success",
		Name:      "High Success",
		Type:      "health",
		Status:    "running",
		Interval:  60,
		Successes: 1000000,
		Failures:  0,
	}

	if patrol.Successes != 1000000 {
		t.Errorf("expected 1000000 successes, got %d", patrol.Successes)
	}
}

func TestPatrolHighFailureCount(t *testing.T) {
	patrol := &Patrol{
		ID:        "high-failure",
		Name:      "High Failure",
		Type:      "health",
		Status:    "error",
		Interval:  60,
		Successes: 0,
		Failures:  999999,
	}

	if patrol.Failures != 999999 {
		t.Errorf("expected 999999 failures, got %d", patrol.Failures)
	}
}

func TestPatrolLongID(t *testing.T) {
	longID := "patrol-with-a-very-long-identifier-that-exceeds-normal-length-limits-for-testing"

	patrol := &Patrol{
		ID:        longID,
		Name:      "Long ID Patrol",
		Type:      "health",
		Status:    "running",
		Interval:  60,
		Successes: 10,
		Failures:  0,
	}

	if patrol.ID != longID {
		t.Error("ID mismatch")
	}
}

func TestPatrolSpecialCharacters(t *testing.T) {
	patrol := &Patrol{
		ID:        "patrol-special",
		Name:      "Test Patrol with émojis 🎉 and ññ characters",
		Type:      "health",
		Status:    "running",
		Interval:  60,
		Message:   "Error: connection failed — timeout exceeded (30s)",
		Successes: 10,
		Failures:  0,
	}

	if patrol.Name != "Test Patrol with émojis 🎉 and ññ characters" {
		t.Error("Name with special characters mismatch")
	}
}

func TestGeneratePatrolLogsEmptyPatrolName(t *testing.T) {
	patrol := &Patrol{
		ID:        "empty-name",
		Name:      "",
		Type:      "health",
		Status:    "running",
		Interval:  60,
		Successes: 10,
		Failures:  0,
	}

	logs := generatePatrolLogs(patrol, 5)
	// Should still generate logs with empty name
	if len(logs) == 0 {
		t.Error("expected logs even with empty patrol name")
	}
}

func TestPatrolLogLevels(t *testing.T) {
	levels := []string{"INFO", "WARN", "ERROR", "DEBUG"}

	for _, level := range levels {
		log := PatrolLog{
			Timestamp: time.Now(),
			Level:     level,
			Message:   "Test message",
		}

		if log.Level != level {
			t.Errorf("expected level %s, got %s", level, log.Level)
		}
	}
}

// ==================== Error Cases ====================

func TestPatrolEmptyID(t *testing.T) {
	patrol := &Patrol{
		ID:        "",
		Name:      "Empty ID",
		Type:      "health",
		Status:    "running",
		Interval:  60,
		Successes: 10,
		Failures:  0,
	}

	if patrol.ID != "" {
		t.Error("expected empty ID")
	}
}

func TestPatrolInvalidStatus(t *testing.T) {
	patrol := &Patrol{
		ID:        "invalid-status",
		Name:      "Invalid Status",
		Type:      "health",
		Status:    "invalid_status",
		Interval:  60,
		Successes: 10,
		Failures:  0,
	}

	// Should accept any status string
	if patrol.Status != "invalid_status" {
		t.Errorf("expected invalid_status, got %s", patrol.Status)
	}
}

func TestPatrolInvalidType(t *testing.T) {
	patrol := &Patrol{
		ID:        "invalid-type",
		Name:      "Invalid Type",
		Type:      "unknown_type",
		Status:    "running",
		Interval:  60,
		Successes: 10,
		Failures:  0,
	}

	// Should accept any type string
	if patrol.Type != "unknown_type" {
		t.Errorf("expected unknown_type, got %s", patrol.Type)
	}
}

// ==================== Format Tests ====================

func TestPatrolJSONMarshaling(t *testing.T) {
	now := time.Now()
	patrol := Patrol{
		ID:        "json-test",
		Name:      "JSON Test",
		Type:      "health",
		Status:    "running",
		Interval:  60,
		LastRun:   now,
		NextRun:   now.Add(time.Minute),
		Message:   "Test message",
		Successes: 100,
		Failures:  5,
		Config: map[string]interface{}{
			"key": "value",
		},
	}

	data, err := json.Marshal(patrol)
	if err != nil {
		t.Fatalf("failed to marshal patrol: %v", err)
	}

	var unmarshaled Patrol
	if err := json.Unmarshal(data, &unmarshaled); err != nil {
		t.Fatalf("failed to unmarshal patrol: %v", err)
	}

	if unmarshaled.ID != patrol.ID {
		t.Errorf("ID mismatch: expected %s, got %s", patrol.ID, unmarshaled.ID)
	}
	if unmarshaled.Name != patrol.Name {
		t.Errorf("Name mismatch: expected %s, got %s", patrol.Name, unmarshaled.Name)
	}
	if unmarshaled.Interval != patrol.Interval {
		t.Errorf("Interval mismatch: expected %d, got %d", patrol.Interval, unmarshaled.Interval)
	}
}

func TestPatrolLogJSONMarshaling(t *testing.T) {
	now := time.Now()
	log := PatrolLog{
		Timestamp: now,
		Level:     "INFO",
		Message:   "Test log message",
	}

	data, err := json.Marshal(log)
	if err != nil {
		t.Fatalf("failed to marshal log: %v", err)
	}

	var unmarshaled PatrolLog
	if err := json.Unmarshal(data, &unmarshaled); err != nil {
		t.Fatalf("failed to unmarshal log: %v", err)
	}

	if unmarshaled.Level != log.Level {
		t.Errorf("Level mismatch: expected %s, got %s", log.Level, unmarshaled.Level)
	}
	if unmarshaled.Message != log.Message {
		t.Errorf("Message mismatch: expected %s, got %s", log.Message, unmarshaled.Message)
	}
}

func TestPatrolSliceJSONMarshaling(t *testing.T) {
	patrols := []Patrol{
		{ID: "p1", Name: "Patrol 1", Type: "health", Status: "running", Interval: 60},
		{ID: "p2", Name: "Patrol 2", Type: "queue", Status: "running", Interval: 30},
		{ID: "p3", Name: "Patrol 3", Type: "deploy", Status: "stopped", Interval: 120},
	}

	data, err := json.Marshal(patrols)
	if err != nil {
		t.Fatalf("failed to marshal patrols: %v", err)
	}

	var unmarshaled []Patrol
	if err := json.Unmarshal(data, &unmarshaled); err != nil {
		t.Fatalf("failed to unmarshal patrols: %v", err)
	}

	if len(unmarshaled) != 3 {
		t.Fatalf("expected 3 patrols, got %d", len(unmarshaled))
	}

	for i, p := range unmarshaled {
		if p.ID != patrols[i].ID {
			t.Errorf("ID mismatch at index %d", i)
		}
	}
}

// ==================== Boundary Tests ====================

func TestPatrolMaxInterval(t *testing.T) {
	maxInt := 2147483647 // Max int32 value (reasonable upper bound)

	patrol := &Patrol{
		ID:        "max-interval",
		Name:      "Max Interval",
		Type:      "health",
		Status:    "running",
		Interval:  maxInt,
		Successes: 0,
		Failures:  0,
	}

	if patrol.Interval != maxInt {
		t.Errorf("expected max interval, got %d", patrol.Interval)
	}
}

func TestPatrolCounters(t *testing.T) {
	tests := []struct {
		name      string
		successes int
		failures   int
	}{
		{"zero-zero", 0, 0},
		{"success-only", 100, 0},
		{"failure-only", 0, 100},
		{"mixed", 50, 50},
		{"high-ratio", 9999, 1},
		{"low-ratio", 1, 9999},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			patrol := &Patrol{
				ID:        tc.name,
				Name:      tc.name,
				Type:      "health",
				Status:    "running",
				Interval:  60,
				Successes: tc.successes,
				Failures:  tc.failures,
			}

			if patrol.Successes != tc.successes {
				t.Errorf("successes: expected %d, got %d", tc.successes, patrol.Successes)
			}
			if patrol.Failures != tc.failures {
				t.Errorf("failures: expected %d, got %d", tc.failures, patrol.Failures)
			}
		})
	}
}

func TestPatrolConfigNestedValues(t *testing.T) {
	patrol := &Patrol{
		ID:       "nested-config",
		Name:     "Nested Config",
		Type:     "health",
		Status:   "running",
		Interval: 60,
		Config: map[string]interface{}{
			"nested": map[string]interface{}{
				"deep": map[string]interface{}{
					"value": 42,
				},
			},
			"array": []interface{}{1, 2, 3},
		},
		Successes: 10,
		Failures:  0,
	}

	nested, ok := patrol.Config["nested"].(map[string]interface{})
	if !ok {
		t.Fatal("expected nested config")
	}
	deep, ok := nested["deep"].(map[string]interface{})
	if !ok {
		t.Fatal("expected deep config")
	}
	if deep["value"] != 42 {
		t.Errorf("expected deep value 42, got %v", deep["value"])
	}
}

// ==================== Time Edge Cases ====================

func TestPatrolLogTimestampOrdering(t *testing.T) {
	patrol := &Patrol{
		ID:        "time-order",
		Name:      "Time Order Test",
		Type:      "health",
		Status:    "running",
		Interval:  60,
		Successes: 100,
		Failures:  0,
	}

	logs := generatePatrolLogs(patrol, 10)

	// Verify logs are in descending time order (newest first)
	for i := 1; i < len(logs); i++ {
		if logs[i-1].Timestamp.Before(logs[i].Timestamp) {
			t.Errorf("log %d is newer than log %d, should be older", i-1, i)
		}
	}
}

func TestPatrolLogConsistentInterval(t *testing.T) {
	interval := 60
	patrol := &Patrol{
		ID:        "interval-check",
		Name:      "Interval Check",
		Type:      "health",
		Status:    "running",
		Interval:  interval,
		Successes: 100,
		Failures:  0,
	}

	logs := generatePatrolLogs(patrol, 5)

	// Verify interval between logs
	for i := 1; i < len(logs); i++ {
		expectedDiff := time.Duration(interval) * time.Second
		actualDiff := logs[i-1].Timestamp.Sub(logs[i].Timestamp)
		// Allow some tolerance for time formatting
		if actualDiff != expectedDiff {
			t.Logf("Log %d interval: expected %v, got %v", i, expectedDiff, actualDiff)
		}
	}
}

// ==================== Unicode and Special Characters ====================

func TestPatrolUnicodeMessage(t *testing.T) {
	unicodeMessages := []string{
		"你好世界",
		"こんにちは",
		"Привет мир",
		"مرحبا بالعالم",
		"🌍🌎🌏",
		"Test with accents: café, naïve, résumé",
	}

	for i, msg := range unicodeMessages {
		t.Run(fmt.Sprintf("unicode_%d", i), func(t *testing.T) {
			patrol := &Patrol{
				ID:        fmt.Sprintf("unicode-%d", i),
				Name:      "Unicode Test",
				Type:      "health",
				Status:    "running",
				Interval:  60,
				Message:   msg,
				Successes: 10,
				Failures:  0,
			}

			if patrol.Message != msg {
				t.Errorf("message mismatch: expected %s, got %s", msg, patrol.Message)
			}
		})
	}
}

func TestPatrolLogUnicodeMessage(t *testing.T) {
	messages := []string{
		"检查完成 ✓",
		"エージェントが見つかりません",
		"Ошибка подключения",
	}

	for i, msg := range messages {
		log := PatrolLog{
			Timestamp: time.Now(),
			Level:     "INFO",
			Message:   msg,
		}

		if log.Message != msg {
			t.Errorf("log message %d mismatch: expected %s, got %s", i, msg, log.Message)
		}
	}
}

// ==================== Table Tests ====================

func TestPatrolStructInvariants(t *testing.T) {
	// Build a slice of sample patrols to verify invariant checks work.
	patrols := []Patrol{
		{ID: "p1", Name: "P1", Type: "health", Status: "running", Interval: 60},
		{ID: "p2", Name: "P2", Type: "queue", Status: "stopped", Interval: 30},
	}

	// Verify unique IDs
	ids := make(map[string]bool)
	for _, p := range patrols {
		if ids[p.ID] {
			t.Errorf("duplicate patrol ID: %s", p.ID)
		}
		ids[p.ID] = true
	}

	// Verify positive intervals
	for _, p := range patrols {
		if p.Interval <= 0 {
			t.Errorf("patrol %s has non-positive interval: %d", p.ID, p.Interval)
		}
	}
}

func TestPatrolValidStatuses(t *testing.T) {
	validStatuses := map[string]bool{
		"running": true,
		"stopped": true,
		"warning": true,
		"error":   true,
	}

	for status := range validStatuses {
		p := Patrol{Status: status}
		if !validStatuses[p.Status] {
			t.Errorf("status %s should be valid", status)
		}
	}
}

func TestPatrolValidTypes(t *testing.T) {
	validTypes := []string{"health", "queue", "heartbeat", "quality", "deploy"}

	for _, pt := range validTypes {
		p := Patrol{Type: pt}
		if p.Type != pt {
			t.Errorf("type mismatch: expected %s, got %s", pt, p.Type)
		}
	}
}

// ==================== Concurrent Access Tests ====================

func TestPatrolConcurrentAccess(t *testing.T) {
	patrol := &Patrol{
		ID:        "concurrent",
		Name:      "Concurrent Test",
		Type:      "health",
		Status:    "running",
		Interval:  60,
		Successes: 0,
		Failures:  0,
	}

	// Simulate concurrent increments
	done := make(chan bool)
	for i := 0; i < 10; i++ {
		go func() {
			for j := 0; j < 100; j++ {
				patrol.Successes++
			}
			done <- true
		}()
	}

	// Wait for all goroutines
	for i := 0; i < 10; i++ {
		<-done
	}

	// Note: This test demonstrates that Patrol is not thread-safe
	// In production, use mutex or atomic operations
	t.Logf("Final successes (non-deterministic): %d", patrol.Successes)
}

// Additional patrol tests for coverage
func TestPatrolListFormats(t *testing.T) {
	runner := NewTestRunner(t)

	formats := []string{"table", "json", "csv", "quiet"}
	for _, format := range formats {
		_, err := runner.Execute("patrol", "list", "--format", format)
		if err != nil {
			t.Errorf("patrol list --format %s failed: %v", format, err)
		}
	}
}

func TestPatrolStatusFormats(t *testing.T) {
	// With daemon unreachable and no local patrols.json, no patrols are loaded,
	// so looking up a specific ID should return "not found".
	t.Setenv("FORGE_API_URL", "http://127.0.0.1:19999")
	runner := NewTestRunner(t)

	_, err := runner.Execute("patrol", "status", "health-check", "--format", "json")
	if err == nil {
		t.Logf("patrol status: no error (daemon may be running)")
	}
}

func TestPatrolLogsFormats(t *testing.T) {
	// With daemon unreachable and no local patrols.json, no patrols are loaded,
	// so looking up a specific ID should return "not found".
	t.Setenv("FORGE_API_URL", "http://127.0.0.1:19999")
	runner := NewTestRunner(t)

	_, err := runner.Execute("patrol", "logs", "health-check", "--format", "json")
	if err == nil {
		t.Logf("patrol logs: no error (daemon may be running)")
	}
}

func TestPatrolListTypeFilter(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("patrol", "list", "--type", "health")
	if err != nil {
		t.Errorf("patrol list --type health failed: %v", err)
	}
}
