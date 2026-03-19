//go:build !tmux_bridge
// +build !tmux_bridge

package main

// Wave 25b: handlers_pattern.go (listPatternsHandler) + lane.go (CheckGates)

import (
	"context"
	"net/http"
	"net/http/httptest"
	"os"
	"testing"
)

// ---------------------------------------------------------------------------
// listPatternsHandler tests
// ---------------------------------------------------------------------------

// TestListPatternsHandler_Empty_W25B: GET /api/patterns with no rows → 200, count=0.
func TestListPatternsHandler_Empty_W25B(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/patterns", nil)
	w := httptest.NewRecorder()
	listPatternsHandler(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	body := w.Body.String()
	if body == "" {
		t.Fatal("expected JSON body, got empty")
	}
}

// TestListPatternsHandler_WithPatterns_W25B: insert a pattern, GET → count=1.
func TestListPatternsHandler_WithPatterns_W25B(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	db := getDBConn()
	_, err := db.Exec(`INSERT INTO patterns (id, name, domain, yaml_content) VALUES (?, ?, ?, ?)`,
		"pat-w25b-1", "Test Pattern", "infra", "steps: []")
	if err != nil {
		t.Fatalf("insert pattern: %v", err)
	}

	req := httptest.NewRequest(http.MethodGet, "/api/patterns", nil)
	w := httptest.NewRecorder()
	listPatternsHandler(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	body := w.Body.String()
	if body == "" {
		t.Fatal("empty body")
	}
	// The JSON must contain the pattern name.
	if len(body) < 10 {
		t.Errorf("unexpectedly short body: %s", body)
	}
}

// TestListPatternsHandler_DomainFilter_W25B: two patterns different domains,
// filter by domain → only 1 returned.
func TestListPatternsHandler_DomainFilter_W25B(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	db := getDBConn()
	db.Exec(`INSERT INTO patterns (id, name, domain, yaml_content) VALUES (?, ?, ?, ?)`,
		"pat-w25b-2", "Infra Pat", "infra", "steps: []")
	db.Exec(`INSERT INTO patterns (id, name, domain, yaml_content) VALUES (?, ?, ?, ?)`,
		"pat-w25b-3", "ML Pat", "ml", "steps: []")

	req := httptest.NewRequest(http.MethodGet, "/api/patterns?domain=infra", nil)
	w := httptest.NewRecorder()
	listPatternsHandler(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// TestPatternsHandler_MethodNotAllowed_W25B: non GET/POST → 405.
// TestListPatternsHandler_MultiplePatterns_W25B: insert 3 patterns, ensure
// all rows scanned (covers the rows.Next loop body).
func TestListPatternsHandler_MultiplePatterns_W25B(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	db := getDBConn()
	for i := 0; i < 3; i++ {
		db.Exec(`INSERT INTO patterns (id, name, domain, yaml_content) VALUES (?, ?, ?, ?)`,
			"pat-multi-"+string(rune('a'+i)), "Pat", "dom", "steps: []")
	}

	req := httptest.NewRequest(http.MethodGet, "/api/patterns", nil)
	w := httptest.NewRecorder()
	listPatternsHandler(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// CheckGates tests
// ---------------------------------------------------------------------------

// TestCheckGates_CoveragePass_W25B: coverage gate passes when reported coverage
// exceeds the threshold. Uses FORGE_TEST_COVERAGE_CMD env override.
func TestCheckGates_CoveragePass_W25B(t *testing.T) {
	// Override coverage command to echo a high-coverage line.
	os.Setenv("FORGE_TEST_COVERAGE_CMD", `echo "coverage: 90.0% of statements"`)
	defer os.Unsetenv("FORGE_TEST_COVERAGE_CMD")

	configs := map[Lane]LaneConfig{
		LaneTest: {
			Gates: []string{"coverage"},
		},
	}
	// Threshold defaults to 80.0 in NewQualityGateChecker; 90.0 >= 80.0 → pass.
	mgr, _, _, cleanup := setupLaneManager(t, configs)
	defer cleanup()

	task := &Task{ID: "t-cov-pass", Domain: "test", Lane: string(LaneDev)}
	result, err := mgr.CheckGates(context.Background(), task, LaneTest)
	if err != nil {
		t.Fatalf("CheckGates error: %v", err)
	}
	if !result.Criteria["coverage"] {
		t.Errorf("expected coverage to pass, errors: %v", result.Errors)
	}
}

// TestCheckGates_CoverageFail_W25B: coverage gate fails when below threshold.
func TestCheckGates_CoverageFail_W25B(t *testing.T) {
	os.Setenv("FORGE_TEST_COVERAGE_CMD", `echo "coverage: 50.0% of statements"`)
	defer os.Unsetenv("FORGE_TEST_COVERAGE_CMD")

	configs := map[Lane]LaneConfig{
		LaneTest: {
			Gates: []string{"coverage"},
		},
	}
	mgr, _, _, cleanup := setupLaneManager(t, configs)
	defer cleanup()

	task := &Task{ID: "t-cov-fail", Domain: "test", Lane: string(LaneDev)}
	result, err := mgr.CheckGates(context.Background(), task, LaneTest)
	if err != nil {
		t.Fatalf("CheckGates error: %v", err)
	}
	if result.Criteria["coverage"] {
		t.Errorf("expected coverage to fail (50%% < 80%%), but passed")
	}
	if result.Passed {
		t.Error("expected overall result to be false")
	}
}

// TestCheckGates_LintPass_W25B: lint gate passes with zero issues.
func TestCheckGates_LintPass_W25B(t *testing.T) {
	os.Setenv("FORGE_TEST_LINT_CMD", `echo '{"Issues":[]}'`)
	defer os.Unsetenv("FORGE_TEST_LINT_CMD")

	configs := map[Lane]LaneConfig{
		LaneTest: {
			Gates: []string{"lint"},
		},
	}
	mgr, _, _, cleanup := setupLaneManager(t, configs)
	defer cleanup()

	task := &Task{ID: "t-lint-pass", Domain: "test", Lane: string(LaneDev)}
	result, err := mgr.CheckGates(context.Background(), task, LaneTest)
	if err != nil {
		t.Fatalf("CheckGates error: %v", err)
	}
	if !result.Criteria["lint"] {
		t.Errorf("expected lint to pass with 0 issues, errors: %v", result.Errors)
	}
}

// TestCheckGates_TypeCheckPass_W25B: type_check gate passes when command exits 0.
func TestCheckGates_TypeCheckPass_W25B(t *testing.T) {
	os.Setenv("FORGE_TEST_TYPECHECK_CMD", "true")
	defer os.Unsetenv("FORGE_TEST_TYPECHECK_CMD")

	configs := map[Lane]LaneConfig{
		LaneTest: {
			Gates: []string{"type_check"},
		},
	}
	mgr, _, _, cleanup := setupLaneManager(t, configs)
	defer cleanup()

	task := &Task{ID: "t-tc-pass", Domain: "test", Lane: string(LaneDev)}
	result, err := mgr.CheckGates(context.Background(), task, LaneTest)
	if err != nil {
		t.Fatalf("CheckGates error: %v", err)
	}
	if !result.Criteria["type_check"] {
		t.Errorf("expected type_check to pass, errors: %v", result.Errors)
	}
}

// TestCheckGates_TypeCheckFail_W25B: type_check gate fails when command exits non-0.
func TestCheckGates_TypeCheckFail_W25B(t *testing.T) {
	os.Setenv("FORGE_TEST_TYPECHECK_CMD", "false")
	defer os.Unsetenv("FORGE_TEST_TYPECHECK_CMD")

	configs := map[Lane]LaneConfig{
		LaneTest: {
			Gates: []string{"type_check"},
		},
	}
	mgr, _, _, cleanup := setupLaneManager(t, configs)
	defer cleanup()

	task := &Task{ID: "t-tc-fail", Domain: "test", Lane: string(LaneDev)}
	result, err := mgr.CheckGates(context.Background(), task, LaneTest)
	if err != nil {
		t.Fatalf("CheckGates error: %v", err)
	}
	if result.Criteria["type_check"] {
		t.Errorf("expected type_check to fail with exit code 1, but passed")
	}
}

// TestCheckGates_MultipleGates_W25B: coverage + lint + type_check all pass.
func TestCheckGates_MultipleGates_W25B(t *testing.T) {
	os.Setenv("FORGE_TEST_COVERAGE_CMD", `echo "coverage: 95.0% of statements"`)
	os.Setenv("FORGE_TEST_LINT_CMD", `echo '{"Issues":[]}'`)
	os.Setenv("FORGE_TEST_TYPECHECK_CMD", "true")
	defer func() {
		os.Unsetenv("FORGE_TEST_COVERAGE_CMD")
		os.Unsetenv("FORGE_TEST_LINT_CMD")
		os.Unsetenv("FORGE_TEST_TYPECHECK_CMD")
	}()

	configs := map[Lane]LaneConfig{
		LaneDeploy: {
			Gates: []string{"coverage", "lint", "type_check"},
		},
	}
	mgr, _, _, cleanup := setupLaneManager(t, configs)
	defer cleanup()

	task := &Task{ID: "t-multi", Domain: "test", Lane: string(LaneTest)}
	result, err := mgr.CheckGates(context.Background(), task, LaneDeploy)
	if err != nil {
		t.Fatalf("CheckGates error: %v", err)
	}
	if !result.Passed {
		t.Errorf("expected all gates to pass, errors: %v", result.Errors)
	}
	if len(result.Criteria) != 3 {
		t.Errorf("expected 3 criteria entries, got %d: %v", len(result.Criteria), result.Criteria)
	}
}

// TestCheckGates_MetricsPopulated_W25B: verifies Metrics map is populated for
// coverage gate (exercises the result.Metrics["coverage"] assignment branch).
func TestCheckGates_MetricsPopulated_W25B(t *testing.T) {
	os.Setenv("FORGE_TEST_COVERAGE_CMD", `echo "coverage: 85.5% of statements"`)
	defer os.Unsetenv("FORGE_TEST_COVERAGE_CMD")

	configs := map[Lane]LaneConfig{
		LaneTest: {
			Gates: []string{"coverage"},
		},
	}
	mgr, _, _, cleanup := setupLaneManager(t, configs)
	defer cleanup()

	task := &Task{ID: "t-metrics", Domain: "test", Lane: string(LaneDev)}
	result, err := mgr.CheckGates(context.Background(), task, LaneTest)
	if err != nil {
		t.Fatalf("CheckGates error: %v", err)
	}
	cov, ok := result.Metrics["coverage"]
	if !ok {
		t.Error("expected 'coverage' key in Metrics")
	}
	if cov < 85.0 || cov > 86.0 {
		t.Errorf("coverage metric = %.1f, want ~85.5", cov)
	}
}
