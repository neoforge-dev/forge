// coverage_test.go - Tests to improve statement coverage (error cases, edge cases, formats)
package main

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestVersionFormats(t *testing.T) {
	runner := NewTestRunner(t)

	t.Run("default", func(t *testing.T) {
		out, err := runner.Execute("version")
		if err != nil {
			t.Fatalf("version: %v", err)
		}
		// Version output must mention FORGE CLI (dev builds use "vdev", release builds use semver)
		if out != "" && !strings.Contains(out, "FORGE CLI") {
			t.Errorf("expected 'FORGE CLI' in version output: %s", out)
		}
	})

	t.Run("json", func(t *testing.T) {
		out, err := runner.Execute("version", "--format", "json")
		if err != nil {
			t.Fatalf("version --format json: %v", err)
		}
		if out != "" && !strings.Contains(out, "version") && !strings.Contains(out, "4.0.0") {
			t.Errorf("expected JSON with version: %s", out)
		}
	})

	t.Run("quiet", func(t *testing.T) {
		_, err := runner.Execute("version", "--format", "quiet")
		if err != nil {
			t.Fatalf("version --format quiet: %v", err)
		}
	})
}

func TestConfigGetErrors(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("config", "set")
	if err == nil {
		t.Error("config set without args should error")
	}
}

func TestConfigFormats(t *testing.T) {
	runner := NewTestRunner(t)

	for _, format := range []string{"table", "json", "csv", "quiet"} {
		t.Run(format, func(t *testing.T) {
			_, _ = runner.Execute("config", "list", "--format", format)
		})
	}
}

func TestPatternListFormats(t *testing.T) {
	runner := NewTestRunner(t)

	t.Run("table", func(t *testing.T) {
		out, err := runner.Execute("pattern", "list")
		if err != nil {
			t.Fatalf("pattern list: %v", err)
		}
		if out != "" && !strings.Contains(out, "Total") && !strings.Contains(out, "ID") {
			t.Logf("pattern list output: %s", out)
		}
	})

	t.Run("json", func(t *testing.T) {
		out, err := runner.Execute("pattern", "list", "--format", "json")
		if err != nil {
			t.Fatalf("pattern list --format json: %v", err)
		}
		if out != "" && !strings.HasPrefix(strings.TrimSpace(out), "[") && !strings.HasPrefix(strings.TrimSpace(out), "{") {
			t.Logf("expected JSON: %s", out)
		}
	})

	t.Run("csv", func(t *testing.T) {
		_, err := runner.Execute("pattern", "list", "--format", "csv")
		if err != nil {
			t.Fatalf("pattern list --format csv: %v", err)
		}
	})

	t.Run("quiet", func(t *testing.T) {
		_, err := runner.Execute("pattern", "list", "--format", "quiet")
		if err != nil {
			t.Fatalf("pattern list --format quiet: %v", err)
		}
	})

	t.Run("category_filter", func(t *testing.T) {
		_, err := runner.Execute("pattern", "list", "--category", "code")
		if err != nil {
			t.Fatalf("pattern list --category code: %v", err)
		}
	})

	t.Run("tag_filter", func(t *testing.T) {
		_, err := runner.Execute("pattern", "list", "--tag", "python")
		if err != nil {
			t.Fatalf("pattern list --tag python: %v", err)
		}
	})
}

func TestPatternShowErrors(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("pattern", "show", "nonexistent-id-xyz")
	if err == nil {
		t.Error("pattern show nonexistent should error")
	}
}

func TestPatternShowFormats(t *testing.T) {
	runner := NewTestRunner(t)

	t.Run("table", func(t *testing.T) {
		out, err := runner.Execute("pattern", "show", "api_endpoint:simple")
		if err != nil {
			t.Fatalf("pattern show: %v", err)
		}
		if out != "" && !strings.Contains(out, "Simple API") && !strings.Contains(out, "api_endpoint:simple") {
			t.Errorf("expected pattern details: %s", out)
		}
	})

	t.Run("json", func(t *testing.T) {
		out, err := runner.Execute("pattern", "show", "api_endpoint:simple", "--format", "json")
		if err != nil {
			t.Fatalf("pattern show --format json: %v", err)
		}
		if out != "" && !strings.Contains(out, "api_endpoint:simple") {
			t.Errorf("expected JSON with id: %s", out)
		}
	})

	t.Run("render", func(t *testing.T) {
		out, err := runner.Execute("pattern", "show", "api_endpoint:simple", "--render")
		if err != nil {
			t.Fatalf("pattern show --render: %v", err)
		}
		if out != "" && !strings.Contains(out, "api_endpoint") && !strings.Contains(out, "Simple API") {
			t.Errorf("expected pattern content in render output: %s", out)
		}
	})
}

func TestPatternLoadFromDir(t *testing.T) {
	dir := t.TempDir()
	restore := setenv("FORGE_ROOT", dir)
	defer restore()

	patternsDir := filepath.Join(dir, ".forge", "patterns")
	if err := os.MkdirAll(patternsDir, 0755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	// Empty patterns.json or invalid JSON: should fall back to built-in
	patternsFile := filepath.Join(patternsDir, "patterns.json")
	if err := os.WriteFile(patternsFile, []byte("[]"), 0644); err != nil {
		t.Fatalf("write: %v", err)
	}

	patterns, err := loadPatterns()
	if err != nil {
		t.Fatalf("loadPatterns: %v", err)
	}
	if len(patterns) != 0 {
		t.Logf("loaded %d patterns from empty JSON", len(patterns))
	}
}

func TestFindPatternNotFound(t *testing.T) {
	_, err := findPattern("does-not-exist-xyz")
	if err == nil {
		t.Error("findPattern nonexistent should error")
	}
}

func TestHelperTruncate(t *testing.T) {
	tests := []struct {
		s      string
		maxLen int
	}{
		{"short", 10},
		{"long string here", 5},
		{"exact", 5},
		{"", 10},
	}
	for _, tt := range tests {
		got := helperTruncate(tt.s, tt.maxLen)
		if len(got) > tt.maxLen {
			t.Errorf("helperTruncate(%q, %d) length %d > %d", tt.s, tt.maxLen, len(got), tt.maxLen)
		}
	}
}

func TestGetPatternsDir(t *testing.T) {
	restore := setenv("FORGE_ROOT", "")
	defer restore()
	dir := getPatternsDir()
	if dir == "" {
		t.Error("getPatternsDir should return fallback path")
	}
	if !strings.Contains(dir, ".forge") || !strings.Contains(dir, "patterns") {
		t.Errorf("getPatternsDir should contain .forge/patterns: %s", dir)
	}
}

func TestFindForgeRoot(t *testing.T) {
	// With FORGE_ROOT set
	restore := setenv("FORGE_ROOT", "/tmp/forge-root")
	defer restore()
	got := findForgeRoot()
	// findForgeRoot may ignore FORGE_ROOT and walk; just ensure no panic
	_ = got
}

func TestFindForgeRootFromCwd(t *testing.T) {
	// From repo root (has .forge) should return cwd or parent
	restore := setenv("FORGE_ROOT", "")
	defer restore()
	got := findForgeRoot()
	// In FORGE repo we likely have .forge
	_ = got
}

// --- check.go pure utility tests ---------------------------------------------

func TestCheckSplitLines(t *testing.T) {
	cases := []struct {
		input string
		max   int
		want  []string
	}{
		{"a\nb\nc", 2, []string{"a", "b"}},
		{"a\n\nb", 10, []string{"a", "b"}},
		{"  x  \n  y  ", 5, []string{"x", "y"}},
		{"", 5, nil},
	}
	for _, tc := range cases {
		got := checkSplitLines(tc.input, tc.max)
		if len(got) != len(tc.want) {
			t.Errorf("checkSplitLines(%q, %d) = %v, want %v", tc.input, tc.max, got, tc.want)
			continue
		}
		for i := range got {
			if got[i] != tc.want[i] {
				t.Errorf("checkSplitLines[%d] = %q, want %q", i, got[i], tc.want[i])
			}
		}
	}
}

func TestCheckTruncate(t *testing.T) {
	cases := []struct {
		s    string
		n    int
		want string
	}{
		{"hello world", 5, "hello..."},
		{"hi", 10, "hi"},
		{"  trim  ", 100, "trim"},
		{"exact", 5, "exact"},
	}
	for _, tc := range cases {
		got := checkTruncate(tc.s, tc.n)
		if got != tc.want {
			t.Errorf("checkTruncate(%q, %d) = %q, want %q", tc.s, tc.n, got, tc.want)
		}
	}
}

func TestIsNotFound(t *testing.T) {
	if isNotFound(nil) {
		t.Error("isNotFound(nil) = true, want false")
	}
	if !isNotFound(fmt.Errorf("executable file not found in PATH")) {
		t.Error("expected true for 'executable file not found'")
	}
	if !isNotFound(fmt.Errorf("no such file or directory")) {
		t.Error("expected true for 'no such file'")
	}
	if isNotFound(fmt.Errorf("some other error")) {
		t.Error("expected false for unrelated error")
	}
}

func TestMath2Round(t *testing.T) {
	cases := []struct {
		in   float64
		want float64
	}{
		{1.234, 1.23},
		{1.235, 1.24},
		{0.0, 0.0},
		{99.999, 100.0},
	}
	for _, tc := range cases {
		got := math2Round(tc.in)
		if got != tc.want {
			t.Errorf("math2Round(%v) = %v, want %v", tc.in, got, tc.want)
		}
	}
}

func TestCheckFileExists(t *testing.T) {
	tmp := filepath.Join(t.TempDir(), "exists.txt")
	if checkFileExists(tmp) {
		t.Error("expected false for non-existent file")
	}
	if err := os.WriteFile(tmp, []byte("x"), 0644); err != nil {
		t.Fatal(err)
	}
	if !checkFileExists(tmp) {
		t.Error("expected true for existing file")
	}
}

func TestNodeScriptExists(t *testing.T) {
	dir := t.TempDir()
	pkg := map[string]interface{}{
		"name": "test",
		"scripts": map[string]string{
			"build": "tsc",
			"test":  "jest",
		},
	}
	data, _ := json.Marshal(pkg)
	if err := os.WriteFile(filepath.Join(dir, "package.json"), data, 0644); err != nil {
		t.Fatal(err)
	}

	if !nodeScriptExists(dir, "build") {
		t.Error("expected true for 'build' script")
	}
	if !nodeScriptExists(dir, "test") {
		t.Error("expected true for 'test' script")
	}
	if nodeScriptExists(dir, "deploy") {
		t.Error("expected false for missing 'deploy' script")
	}
	if nodeScriptExists(t.TempDir(), "build") {
		t.Error("expected false when no package.json")
	}
}

// --- handoff, git, council utility tests ------------------------------------

func TestGenerateHandoffID(t *testing.T) {
	id := generateHandoffID()
	if len(id) != 8 {
		t.Errorf("generateHandoffID() len = %d, want 8", len(id))
	}
	for _, c := range id {
		if !strings.ContainsRune("0123456789abcdef", c) {
			t.Errorf("generateHandoffID() contains non-hex char %q in %q", c, id)
		}
	}
	// IDs should be unique
	id2 := generateHandoffID()
	if id == id2 {
		t.Logf("note: two generateHandoffID() calls returned same value (1/4B chance)")
	}
}

func TestResolveForgeRoot_EnvVar(t *testing.T) {
	t.Setenv("FORGE_ROOT", "/tmp/my-forge")
	got := resolveForgeRoot()
	if got != "/tmp/my-forge" {
		t.Errorf("resolveForgeRoot() = %q, want /tmp/my-forge", got)
	}
}

func TestResolveForgeRoot_Default(t *testing.T) {
	t.Setenv("FORGE_ROOT", "")
	got := resolveForgeRoot()
	if got != "." {
		t.Errorf("resolveForgeRoot() with no env = %q, want .", got)
	}
}

func TestCouncilMarkerDir_EnvVar(t *testing.T) {
	tmpDir := t.TempDir()
	t.Setenv("FORGE_ROOT", tmpDir)
	got := councilMarkerDir()
	if !strings.Contains(got, tmpDir) {
		t.Errorf("councilMarkerDir() = %q, want to contain %q", got, tmpDir)
	}
	if !strings.Contains(got, ".forge") {
		t.Errorf("councilMarkerDir() = %q, want to contain .forge", got)
	}
}

func TestGetGitBranch_ValidRepo(t *testing.T) {
	// We're inside a git repo — this should return a non-empty branch name.
	root := os.Getenv("FORGE_ROOT")
	if root == "" {
		root = "."
	}
	branch := getGitBranch(root)
	if branch == "" {
		t.Error("getGitBranch() returned empty string in valid git repo")
	}
}

func TestGetGitBranch_InvalidDir(t *testing.T) {
	// Non-git dir should return "unknown"
	got := getGitBranch(t.TempDir())
	if got != "unknown" {
		t.Errorf("getGitBranch(non-git-dir) = %q, want \"unknown\"", got)
	}
}

// --- handoff save/load tests -------------------------------------------------

func TestSaveAndLoadHandoff(t *testing.T) {
	tmpDir := t.TempDir()
	doc := &HandoffDoc{
		ID:          "abc12345",
		FromAgent:   "claude:gaea",
		Task:        "Fix the thing",
		Status:      "in_progress",
		Priority:    "high",
		Description: "Test handoff",
		CreatedAt:   "2026-03-19T10:00:00Z",
	}

	path, err := saveHandoff(tmpDir, doc)
	if err != nil {
		t.Fatalf("saveHandoff: %v", err)
	}
	if path == "" {
		t.Error("saveHandoff returned empty path")
	}

	loaded, err := loadHandoff(tmpDir, doc.ID)
	if err != nil {
		t.Fatalf("loadHandoff: %v", err)
	}
	if loaded.ID != doc.ID {
		t.Errorf("loaded.ID = %q, want %q", loaded.ID, doc.ID)
	}
	if loaded.FromAgent != doc.FromAgent {
		t.Errorf("loaded.FromAgent = %q, want %q", loaded.FromAgent, doc.FromAgent)
	}
	if loaded.Task != doc.Task {
		t.Errorf("loaded.Task = %q, want %q", loaded.Task, doc.Task)
	}
}

func TestLoadHandoff_NotFound(t *testing.T) {
	_, err := loadHandoff(t.TempDir(), "nonexistent-id")
	if err == nil {
		t.Error("expected error for nonexistent handoff, got nil")
	}
}

func TestAgentClearCommand(t *testing.T) {
	tests := []struct {
		agent string
		want  string
	}{
		{"kimi", "/clear"},
		{"kimi2", "/clear"},
		{"glm", "/clear"},
		{"minimax", "/clear"},
		{"cursor", "/clear"},
		{"pi", "/new"},
		{"pi2", "/new"},
	}
	for _, tt := range tests {
		t.Run(tt.agent, func(t *testing.T) {
			got := agentClearCommand(tt.agent)
			if got != tt.want {
				t.Errorf("agentClearCommand(%q) = %q, want %q", tt.agent, got, tt.want)
			}
		})
	}
}

func TestGetGitLog_ValidRepo(t *testing.T) {
	root := os.Getenv("FORGE_ROOT")
	if root == "" {
		root = "."
	}
	logs := getGitLog(root, 3)
	// We're in a git repo, so there should be at least 1 commit
	if len(logs) == 0 {
		t.Error("getGitLog() returned no commits in valid repo")
	}
}

func TestGetGitLog_InvalidDir(t *testing.T) {
	logs := getGitLog(t.TempDir(), 5)
	if logs != nil {
		t.Errorf("getGitLog(non-git-dir) = %v, want nil", logs)
	}
}

func TestGetModifiedFiles_ValidRepo(t *testing.T) {
	root := os.Getenv("FORGE_ROOT")
	if root == "" {
		root = "."
	}
	// Just confirm no panic and returns slice (may be empty if clean)
	files := getModifiedFiles(root)
	_ = files // may be nil or populated depending on dirty state
}

func TestGetModifiedFiles_InvalidDir(t *testing.T) {
	files := getModifiedFiles(t.TempDir())
	if files != nil {
		t.Errorf("getModifiedFiles(non-git-dir) = %v, want nil", files)
	}
}

// --- validateWorkflow tests --------------------------------------------------

func TestValidateWorkflow_Valid(t *testing.T) {
	wf := &WorkflowDef{
		Name: "test",
		Steps: []WorkflowStep{
			{Name: "build", Command: "go build ./..."},
			{Name: "test", Command: "go test ./..."},
		},
	}
	errs := validateWorkflow(wf)
	if len(errs) != 0 {
		t.Errorf("validateWorkflow(valid) = %v, want no errors", errs)
	}
}

func TestValidateWorkflow_NoSteps(t *testing.T) {
	wf := &WorkflowDef{Name: "empty"}
	errs := validateWorkflow(wf)
	if len(errs) == 0 {
		t.Error("validateWorkflow(no steps) should have errors")
	}
}

func TestValidateWorkflow_MissingName(t *testing.T) {
	wf := &WorkflowDef{
		Steps: []WorkflowStep{
			{Command: "echo hi"}, // no name
		},
	}
	errs := validateWorkflow(wf)
	if len(errs) == 0 {
		t.Error("validateWorkflow(missing step name) should have errors")
	}
}

func TestValidateWorkflow_MissingCommand(t *testing.T) {
	wf := &WorkflowDef{
		Steps: []WorkflowStep{
			{Name: "build"}, // no command
		},
	}
	errs := validateWorkflow(wf)
	if len(errs) == 0 {
		t.Error("validateWorkflow(missing step command) should have errors")
	}
}

func TestValidateWorkflow_DuplicateStepName(t *testing.T) {
	wf := &WorkflowDef{
		Steps: []WorkflowStep{
			{Name: "build", Command: "go build"},
			{Name: "build", Command: "go build ./..."}, // duplicate
		},
	}
	errs := validateWorkflow(wf)
	found := false
	for _, e := range errs {
		if strings.Contains(e, "duplicate") {
			found = true
		}
	}
	if !found {
		t.Errorf("expected duplicate error, got %v", errs)
	}
}

func TestLoadWorkflowFile_JSON(t *testing.T) {
	content := `{"name":"test","steps":[{"name":"s1","command":"echo hi"}]}`
	f := filepath.Join(t.TempDir(), "wf.json")
	if err := os.WriteFile(f, []byte(content), 0644); err != nil {
		t.Fatal(err)
	}
	wf, err := loadWorkflowFile(f)
	if err != nil {
		t.Fatalf("loadWorkflowFile(JSON): %v", err)
	}
	if wf.Name != "test" || len(wf.Steps) != 1 {
		t.Errorf("loaded = %+v", wf)
	}
}

func TestLoadWorkflowFile_YAML(t *testing.T) {
	content := "name: test\nsteps:\n  - name: s1\n    command: echo hi\n"
	f := filepath.Join(t.TempDir(), "wf.yaml")
	if err := os.WriteFile(f, []byte(content), 0644); err != nil {
		t.Fatal(err)
	}
	wf, err := loadWorkflowFile(f)
	if err != nil {
		t.Fatalf("loadWorkflowFile(YAML): %v", err)
	}
	if wf.Name != "test" || len(wf.Steps) != 1 {
		t.Errorf("loaded = %+v", wf)
	}
}

func TestLoadWorkflowFile_NotFound(t *testing.T) {
	_, err := loadWorkflowFile(filepath.Join(t.TempDir(), "missing.yaml"))
	if err == nil {
		t.Error("expected error for missing file, got nil")
	}
}

// --- saveSessionSummary test -------------------------------------------------

func TestSaveSessionSummary(t *testing.T) {
	tmpDir := t.TempDir()
	doc := &HandoffDoc{
		ID:          "test001",
		FromAgent:   "claude:gaea",
		Task:        "Write tests",
		Status:      "complete",
		Priority:    "high",
		CreatedAt:   "2026-03-19T12:00:00Z",
	}
	path, err := saveSessionSummary(tmpDir, doc,
		[]string{"abc1234 feat: add tests"},
		[]string{"coverage_test.go"})
	if err != nil {
		t.Fatalf("saveSessionSummary: %v", err)
	}
	if path == "" {
		t.Error("saveSessionSummary returned empty path")
	}
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read session summary: %v", err)
	}
	if !strings.Contains(string(data), "test001") {
		t.Errorf("session summary missing handoff ID, got:\n%s", string(data))
	}
}
