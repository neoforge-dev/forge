//go:build !tmux_bridge

// scaffold_test.go — tests for forge scaffold command
package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/spf13/cobra"
)

// newScaffoldRunner builds a minimal test root with only scaffoldCmd registered,
// avoiding daemon/network commands that cause hangs in CI.
func newScaffoldRunner(t *testing.T) *TestRunner {
	t.Helper()
	root := &cobra.Command{
		Use:   "forge",
		Short: "FORGE CLI (scaffold test)",
		Run:   func(cmd *cobra.Command, args []string) {},
	}
	root.PersistentFlags().StringVar(&outputFormat, "format", "table", "output format")
	root.AddCommand(scaffoldCmd)
	return &TestRunner{t: t, rootCmd: root}
}

// ---------------------------------------------------------------------------
// Unit tests for stack file generators
// ---------------------------------------------------------------------------

func TestFastAPIFilesStructure(t *testing.T) {
	files := fastAPIFiles("my-api", "vc")

	wantPaths := []string{
		"backend/app/api/routes/.gitkeep",
		"backend/app/core/config.py",
		"backend/app/main.py",
		"backend/app/models/.gitkeep",
		"backend/alembic/versions/.gitkeep",
		"backend/alembic.ini",
		"backend/pyproject.toml",
		"backend/Dockerfile",
		"backend/entrypoint.sh",
		"backend/railway.toml",
	}

	if len(files) != len(wantPaths) {
		t.Fatalf("fastAPIFiles returned %d files, want %d", len(files), len(wantPaths))
	}
	for i, f := range files {
		if f.path != wantPaths[i] {
			t.Errorf("file[%d].path = %q, want %q", i, f.path, wantPaths[i])
		}
	}
}

func TestFastAPIConfigPyHasForgeImports(t *testing.T) {
	files := fastAPIFiles("svc", "")
	var configContent string
	for _, f := range files {
		if f.path == "backend/app/core/config.py" {
			configContent = f.content
			break
		}
	}
	if configContent == "" {
		t.Fatal("config.py not found in fastAPIFiles output")
	}

	wantImports := []string{
		"from forge_shared.auth import ForgeAuth",
		"from forge_shared.jobs.queue import JobQueue",
	}
	for _, imp := range wantImports {
		if !strings.Contains(configContent, imp) {
			t.Errorf("config.py missing import: %q", imp)
		}
	}
}

func TestFastAPIEntrypointShContent(t *testing.T) {
	files := fastAPIFiles("svc", "")
	var content string
	for _, f := range files {
		if strings.HasSuffix(f.path, "entrypoint.sh") {
			content = f.content
			break
		}
	}
	if content == "" {
		t.Fatal("entrypoint.sh not found in fastAPIFiles output")
	}
	if !strings.Contains(content, "alembic upgrade head") {
		t.Error("entrypoint.sh must call 'alembic upgrade head'")
	}
	if !strings.Contains(content, "uvicorn") {
		t.Error("entrypoint.sh must start uvicorn")
	}
}

func TestFastAPIRailwayToml(t *testing.T) {
	files := fastAPIFiles("svc", "")
	var content string
	for _, f := range files {
		if f.path == "backend/railway.toml" {
			content = f.content
			break
		}
	}
	if !strings.Contains(content, `builder = "DOCKERFILE"`) {
		t.Error("railway.toml must set builder = DOCKERFILE")
	}
	if !strings.Contains(content, "entrypoint.sh") {
		t.Error("railway.toml startCommand must reference entrypoint.sh")
	}
}

func TestFastAPIDomainComment(t *testing.T) {
	files := fastAPIFiles("svc", "vc")
	var configContent string
	for _, f := range files {
		if f.path == "backend/app/core/config.py" {
			configContent = f.content
			break
		}
	}
	if !strings.Contains(configContent, "# domain: vc") {
		t.Error("config.py should contain domain comment when domain is set")
	}
}

func TestPhoenixFilesStructure(t *testing.T) {
	files := phoenixFiles("my-app", "cs")

	wantPaths := []string{
		"mix.exs",
		"config/config.exs",
		"config/runtime.exs",
		"lib/my-app_web/router.ex",
		"lib/my-app_web/live/.gitkeep",
		"railway.toml",
	}

	if len(files) != len(wantPaths) {
		t.Fatalf("phoenixFiles returned %d files, want %d", len(files), len(wantPaths))
	}
	for i, f := range files {
		if f.path != wantPaths[i] {
			t.Errorf("file[%d].path = %q, want %q", i, f.path, wantPaths[i])
		}
	}
}

func TestPhoenixRuntimeExsHasEnvVars(t *testing.T) {
	files := phoenixFiles("my-app", "")
	var content string
	for _, f := range files {
		if f.path == "config/runtime.exs" {
			content = f.content
			break
		}
	}
	if !strings.Contains(content, "DATABASE_URL") {
		t.Error("runtime.exs must reference DATABASE_URL")
	}
	if !strings.Contains(content, "PORT") {
		t.Error("runtime.exs must reference PORT env var")
	}
}

func TestGoHTMXFilesStructure(t *testing.T) {
	files := goHTMXFiles("my-service", "")

	wantPaths := []string{
		"main.go",
		"Dockerfile",
		"railway.toml",
	}

	if len(files) != len(wantPaths) {
		t.Fatalf("goHTMXFiles returned %d files, want %d", len(files), len(wantPaths))
	}
	for i, f := range files {
		if f.path != wantPaths[i] {
			t.Errorf("file[%d].path = %q, want %q", i, f.path, wantPaths[i])
		}
	}
}

func TestGoHTMXMainGoHasHealthEndpoint(t *testing.T) {
	files := goHTMXFiles("my-service", "")
	var content string
	for _, f := range files {
		if f.path == "main.go" {
			content = f.content
			break
		}
	}
	if !strings.Contains(content, "/health") {
		t.Error("go-htmx main.go must register /health endpoint")
	}
	if !strings.Contains(content, "my-service") {
		t.Error("go-htmx main.go must reference the service name")
	}
}

// ---------------------------------------------------------------------------
// Unit tests for toCamelCase helper
// ---------------------------------------------------------------------------

func TestToCamelCase(t *testing.T) {
	cases := []struct {
		input string
		want  string
	}{
		{"my-app", "MyApp"},
		{"my_service", "MyService"},
		{"voice-coach", "VoiceCoach"},
		{"simpleapp", "Simpleapp"},
		{"", ""},
	}
	for _, tc := range cases {
		got := toCamelCase(tc.input)
		if got != tc.want {
			t.Errorf("toCamelCase(%q) = %q, want %q", tc.input, got, tc.want)
		}
	}
}

// ---------------------------------------------------------------------------
// Integration tests: scaffold command via CLI runner
// ---------------------------------------------------------------------------

func TestScaffoldDryRunFastAPI(t *testing.T) {
	runner := newScaffoldRunner(t)
	out, err := runner.Execute("scaffold", "fastapi", "test-svc", "--dry-run")
	if err != nil {
		t.Fatalf("scaffold dry-run failed: %v\noutput: %s", err, out)
	}
	if !strings.Contains(out, "[dry-run]") {
		t.Error("dry-run output should contain [dry-run]")
	}
	if !strings.Contains(out, "test-svc") {
		t.Error("dry-run output should reference the service name")
	}
	if !strings.Contains(out, "fastapi") {
		t.Error("dry-run output should reference the stack name")
	}
}

func TestScaffoldDryRunPhoenix(t *testing.T) {
	runner := newScaffoldRunner(t)
	out, err := runner.Execute("scaffold", "phoenix", "my-phoenix-app", "--dry-run")
	if err != nil {
		t.Fatalf("scaffold phoenix dry-run failed: %v\noutput: %s", err, out)
	}
	if !strings.Contains(out, "mix.exs") {
		t.Error("phoenix dry-run should list mix.exs")
	}
}

func TestScaffoldDryRunGoHTMX(t *testing.T) {
	runner := newScaffoldRunner(t)
	out, err := runner.Execute("scaffold", "go-htmx", "my-go-app", "--dry-run")
	if err != nil {
		t.Fatalf("scaffold go-htmx dry-run failed: %v\noutput: %s", err, out)
	}
	if !strings.Contains(out, "main.go") {
		t.Error("go-htmx dry-run should list main.go")
	}
}

func TestScaffoldInvalidStack(t *testing.T) {
	runner := newScaffoldRunner(t)
	_, err := runner.Execute("scaffold", "rails", "my-app")
	if err == nil {
		t.Fatal("expected error for invalid stack, got nil")
	}
	if !strings.Contains(err.Error(), "unknown stack") {
		t.Errorf("expected 'unknown stack' error, got: %v", err)
	}
}

func TestScaffoldMissingArgs(t *testing.T) {
	runner := newScaffoldRunner(t)
	_, err := runner.Execute("scaffold", "fastapi")
	if err == nil {
		t.Fatal("expected error when name arg is missing")
	}
}

func TestScaffoldWritesFiles(t *testing.T) {
	tmpDir := t.TempDir()
	origDir, err := os.Getwd()
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Chdir(tmpDir); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.Chdir(origDir) })

	runner := newScaffoldRunner(t)
	out, err := runner.Execute("scaffold", "go-htmx", "acme-svc")
	if err != nil {
		t.Fatalf("scaffold write failed: %v\noutput: %s", err, out)
	}

	serviceRoot := filepath.Join(tmpDir, "services", "acme-svc")
	for _, name := range []string{"main.go", "Dockerfile", "railway.toml"} {
		p := filepath.Join(serviceRoot, name)
		if _, statErr := os.Stat(p); os.IsNotExist(statErr) {
			t.Errorf("expected file %s to be created", p)
		}
	}
}

func TestScaffoldRefusesExistingDir(t *testing.T) {
	tmpDir := t.TempDir()
	origDir, err := os.Getwd()
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Chdir(tmpDir); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.Chdir(origDir) })

	if err := os.MkdirAll(filepath.Join(tmpDir, "services", "existing-svc"), 0755); err != nil {
		t.Fatal(err)
	}

	runner := newScaffoldRunner(t)
	_, err = runner.Execute("scaffold", "fastapi", "existing-svc")
	if err == nil {
		t.Fatal("expected error when service directory already exists")
	}
	if !strings.Contains(err.Error(), "already exists") {
		t.Errorf("expected 'already exists' error, got: %v", err)
	}
}

func TestScaffoldDomainFlagAppearsInOutput(t *testing.T) {
	runner := newScaffoldRunner(t)
	out, err := runner.Execute("scaffold", "fastapi", "my-svc", "--domain", "vc", "--dry-run")
	if err != nil {
		t.Fatalf("scaffold with domain dry-run failed: %v\noutput: %s", err, out)
	}
	if !strings.Contains(out, "vc") {
		t.Error("dry-run output should show the domain value")
	}
}
