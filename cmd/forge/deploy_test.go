// deploy_test.go — tests for forge deploy command
package main

import (
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"
)

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

// writeDeployConfig writes a deploys.yaml to dir and returns its path.
func writeDeployConfig(t *testing.T, dir, content string) string {
	t.Helper()
	cfgDir := filepath.Join(dir, "config")
	if err := os.MkdirAll(cfgDir, 0o755); err != nil {
		t.Fatalf("mkdir config: %v", err)
	}
	path := filepath.Join(cfgDir, "deploys.yaml")
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatalf("write deploys.yaml: %v", err)
	}
	return path
}

const testDeployYAML = `
products:
  interview-simulator:
    domain: codeswiftr-com
    backend:
      type: railway
      directory: services/interview-simulator/backend
      pre_deploy: "alembic upgrade head"
      start_command: "uvicorn app.main:app --host 0.0.0.0 --port $PORT"
    frontend:
      type: cloudflare-pages
      directory: services/interview-simulator/frontend
      build_command: "npm run build"
      output_dir: "dist"
      project_name: "codeswiftr"

  voice-coach:
    domain: brandfocus-ai
    backend:
      type: railway
      directory: services/voice-coach/app/backend
      pre_deploy: "alembic upgrade head"
      start_command: "uvicorn app.main:app --host 0.0.0.0 --port $PORT"

  study-flow:
    domain: thebrightharbor-com
    backend:
      type: railway
      directory: services/study-flow/backend
      pre_deploy: "alembic upgrade head"
      start_command: "uvicorn app.main:app --host 0.0.0.0 --port $PORT"
    frontend:
      type: cloudflare-pages
      directory: services/study-flow/frontend
      build_command: "npm run build"
      output_dir: "dist"
      project_name: "study-flow"
`

// ---------------------------------------------------------------------------
// YAML config parsing
// ---------------------------------------------------------------------------

func TestLoadDeployConfig_ParsesAllProducts(t *testing.T) {
	dir := t.TempDir()
	path := writeDeployConfig(t, dir, testDeployYAML)
	t.Setenv("FORGE_DEPLOY_CONFIG", path)

	cfg, err := loadDeployConfig()
	if err != nil {
		t.Fatalf("loadDeployConfig: %v", err)
	}

	wantProducts := []string{"interview-simulator", "voice-coach", "study-flow"}
	for _, name := range wantProducts {
		if _, ok := cfg.Products[name]; !ok {
			t.Errorf("product %q not found in config", name)
		}
	}
}

func TestLoadDeployConfig_BackendFields(t *testing.T) {
	dir := t.TempDir()
	path := writeDeployConfig(t, dir, testDeployYAML)
	t.Setenv("FORGE_DEPLOY_CONFIG", path)

	cfg, err := loadDeployConfig()
	if err != nil {
		t.Fatalf("loadDeployConfig: %v", err)
	}

	p := cfg.Products["interview-simulator"]
	if p.Backend == nil {
		t.Fatal("interview-simulator backend is nil")
	}
	if p.Backend.Type != "railway" {
		t.Errorf("backend type: got %q, want %q", p.Backend.Type, "railway")
	}
	if p.Backend.PreDeploy != "alembic upgrade head" {
		t.Errorf("pre_deploy: got %q, want %q", p.Backend.PreDeploy, "alembic upgrade head")
	}
	if p.Backend.Directory != "services/interview-simulator/backend" {
		t.Errorf("directory: got %q", p.Backend.Directory)
	}
}

func TestLoadDeployConfig_FrontendFields(t *testing.T) {
	dir := t.TempDir()
	path := writeDeployConfig(t, dir, testDeployYAML)
	t.Setenv("FORGE_DEPLOY_CONFIG", path)

	cfg, err := loadDeployConfig()
	if err != nil {
		t.Fatalf("loadDeployConfig: %v", err)
	}

	p := cfg.Products["interview-simulator"]
	if p.Frontend == nil {
		t.Fatal("interview-simulator frontend is nil")
	}
	if p.Frontend.Type != "cloudflare-pages" {
		t.Errorf("frontend type: got %q, want %q", p.Frontend.Type, "cloudflare-pages")
	}
	if p.Frontend.BuildCommand != "npm run build" {
		t.Errorf("build_command: got %q", p.Frontend.BuildCommand)
	}
	if p.Frontend.OutputDir != "dist" {
		t.Errorf("output_dir: got %q", p.Frontend.OutputDir)
	}
	if p.Frontend.ProjectName != "codeswiftr" {
		t.Errorf("project_name: got %q", p.Frontend.ProjectName)
	}
}

func TestLoadDeployConfig_BackendOnlyProduct(t *testing.T) {
	dir := t.TempDir()
	path := writeDeployConfig(t, dir, testDeployYAML)
	t.Setenv("FORGE_DEPLOY_CONFIG", path)

	cfg, err := loadDeployConfig()
	if err != nil {
		t.Fatalf("loadDeployConfig: %v", err)
	}

	p := cfg.Products["voice-coach"]
	if p.Backend == nil {
		t.Fatal("voice-coach backend is nil")
	}
	if p.Frontend != nil {
		t.Error("voice-coach should have no frontend, got non-nil")
	}
}

func TestLoadDeployConfig_DomainField(t *testing.T) {
	dir := t.TempDir()
	path := writeDeployConfig(t, dir, testDeployYAML)
	t.Setenv("FORGE_DEPLOY_CONFIG", path)

	cfg, err := loadDeployConfig()
	if err != nil {
		t.Fatalf("loadDeployConfig: %v", err)
	}

	tests := []struct {
		product string
		domain  string
	}{
		{"interview-simulator", "codeswiftr-com"},
		{"voice-coach", "brandfocus-ai"},
		{"study-flow", "thebrightharbor-com"},
	}
	for _, tt := range tests {
		p := cfg.Products[tt.product]
		if p.Domain != tt.domain {
			t.Errorf("product %q domain: got %q, want %q", tt.product, p.Domain, tt.domain)
		}
	}
}

func TestLoadDeployConfig_MissingFile(t *testing.T) {
	t.Setenv("FORGE_DEPLOY_CONFIG", "/does/not/exist/deploys.yaml")

	_, err := loadDeployConfig()
	if err == nil {
		t.Fatal("expected error for missing config file, got nil")
	}
	if !strings.Contains(err.Error(), "cannot read deploy config") {
		t.Errorf("error message should mention 'cannot read deploy config': %v", err)
	}
}

func TestLoadDeployConfig_InvalidYAML(t *testing.T) {
	dir := t.TempDir()
	path := writeDeployConfig(t, dir, "products: [invalid yaml: {{{")
	t.Setenv("FORGE_DEPLOY_CONFIG", path)

	_, err := loadDeployConfig()
	if err == nil {
		t.Fatal("expected error for invalid YAML, got nil")
	}
}

// ---------------------------------------------------------------------------
// Dry-run output
// ---------------------------------------------------------------------------

func captureDeployDryRun(t *testing.T, productName string, product DeployProduct) string {
	t.Helper()
	// Redirect stdout to capture output
	old := os.Stdout
	r, w, err := os.Pipe()
	if err != nil {
		t.Fatalf("pipe: %v", err)
	}
	os.Stdout = w

	opts := deployOptions{dryRun: true}
	if deployErr := deployProduct(productName, product, opts); deployErr != nil {
		w.Close()
		os.Stdout = old
		t.Fatalf("deployProduct: %v", deployErr)
	}

	w.Close()
	os.Stdout = old

	buf := make([]byte, 8192)
	n, _ := r.Read(buf)
	return string(buf[:n])
}

func TestDeployDryRun_InterviewSimulator(t *testing.T) {
	dir := t.TempDir()
	path := writeDeployConfig(t, dir, testDeployYAML)
	t.Setenv("FORGE_DEPLOY_CONFIG", path)

	cfg, err := loadDeployConfig()
	if err != nil {
		t.Fatalf("loadDeployConfig: %v", err)
	}

	output := captureDeployDryRun(t, "interview-simulator", cfg.Products["interview-simulator"])

	checks := []string{
		"dry-run",
		"pre-deploy",
		"alembic upgrade head",
		"railway",
		"npm run build",
		"wrangler",
		"codeswiftr",
	}
	for _, want := range checks {
		if !strings.Contains(strings.ToLower(output), strings.ToLower(want)) {
			t.Errorf("dry-run output missing %q\nGot:\n%s", want, output)
		}
	}
}

func TestDeployDryRun_VoiceCoach_BackendOnly(t *testing.T) {
	dir := t.TempDir()
	path := writeDeployConfig(t, dir, testDeployYAML)
	t.Setenv("FORGE_DEPLOY_CONFIG", path)

	cfg, err := loadDeployConfig()
	if err != nil {
		t.Fatalf("loadDeployConfig: %v", err)
	}

	old := os.Stdout
	r, w, _ := os.Pipe()
	os.Stdout = w

	opts := deployOptions{dryRun: true, backendOnly: false, frontendOnly: false}
	_ = deployProduct("voice-coach", cfg.Products["voice-coach"], opts)

	w.Close()
	os.Stdout = old
	buf := make([]byte, 4096)
	n, _ := r.Read(buf)
	output := string(buf[:n])

	if !strings.Contains(strings.ToLower(output), "railway") {
		t.Errorf("expected 'railway' in voice-coach dry-run output\nGot:\n%s", output)
	}
	if strings.Contains(strings.ToLower(output), "wrangler") {
		t.Errorf("voice-coach has no frontend — should not mention wrangler\nGot:\n%s", output)
	}
}

func TestDeployDryRun_StudyFlow(t *testing.T) {
	dir := t.TempDir()
	path := writeDeployConfig(t, dir, testDeployYAML)
	t.Setenv("FORGE_DEPLOY_CONFIG", path)

	cfg, err := loadDeployConfig()
	if err != nil {
		t.Fatalf("loadDeployConfig: %v", err)
	}

	output := captureDeployDryRun(t, "study-flow", cfg.Products["study-flow"])

	if !strings.Contains(strings.ToLower(output), "study-flow") {
		t.Errorf("expected product name in output\nGot:\n%s", output)
	}
	if !strings.Contains(strings.ToLower(output), "railway") {
		t.Errorf("expected railway step\nGot:\n%s", output)
	}
	if !strings.Contains(strings.ToLower(output), "wrangler") {
		t.Errorf("expected wrangler step\nGot:\n%s", output)
	}
}

// ---------------------------------------------------------------------------
// Error cases
// ---------------------------------------------------------------------------

func TestDeployCmd_MissingProductName(t *testing.T) {
	dir := t.TempDir()
	path := writeDeployConfig(t, dir, testDeployYAML)
	t.Setenv("FORGE_DEPLOY_CONFIG", path)

	// Simulate RunE with no args and no --all flag
	cmd := deployCmd
	cmd.Flags().Set("all", "false")

	err := cmd.RunE(cmd, []string{})
	if err == nil {
		t.Fatal("expected error for missing product name")
	}
	if !strings.Contains(err.Error(), "product name is required") {
		t.Errorf("error should mention 'product name is required': %v", err)
	}
}

func TestDeployCmd_UnknownProduct(t *testing.T) {
	dir := t.TempDir()
	path := writeDeployConfig(t, dir, testDeployYAML)
	t.Setenv("FORGE_DEPLOY_CONFIG", path)

	cfg, err := loadDeployConfig()
	if err != nil {
		t.Fatalf("loadDeployConfig: %v", err)
	}

	opts := deployOptions{dryRun: true}
	_, ok := cfg.Products["nonexistent-product"]
	if ok {
		t.Fatal("product should not exist")
	}

	// Verify the unknown product error message via the command RunE
	cmd := deployCmd
	cmd.Flags().Set("all", "false")
	cmd.Flags().Set("dry-run", "true")
	runErr := cmd.RunE(cmd, []string{"nonexistent-product"})
	if runErr == nil {
		t.Fatal("expected error for unknown product")
	}
	if !strings.Contains(runErr.Error(), "unknown product") {
		t.Errorf("error should mention 'unknown product': %v", runErr)
	}
	// Recovery hint: should list available products
	if !strings.Contains(runErr.Error(), "Available") {
		t.Errorf("error should list available products: %v", runErr)
	}

	_ = opts
}

func TestDeployCmd_MutuallyExclusiveFlags(t *testing.T) {
	dir := t.TempDir()
	path := writeDeployConfig(t, dir, testDeployYAML)
	t.Setenv("FORGE_DEPLOY_CONFIG", path)

	cmd := deployCmd
	cmd.Flags().Set("backend-only", "true")
	cmd.Flags().Set("frontend-only", "true")
	defer func() {
		cmd.Flags().Set("backend-only", "false")
		cmd.Flags().Set("frontend-only", "false")
	}()

	err := cmd.RunE(cmd, []string{"interview-simulator"})
	if err == nil {
		t.Fatal("expected error when --backend-only and --frontend-only are both set")
	}
	if !strings.Contains(err.Error(), "mutually exclusive") {
		t.Errorf("error should mention 'mutually exclusive': %v", err)
	}
}

// ---------------------------------------------------------------------------
// --all flag lists all products
// ---------------------------------------------------------------------------

func TestDeployCmd_AllFlag_ListsAllProducts(t *testing.T) {
	dir := t.TempDir()
	path := writeDeployConfig(t, dir, testDeployYAML)
	t.Setenv("FORGE_DEPLOY_CONFIG", path)

	cfg, err := loadDeployConfig()
	if err != nil {
		t.Fatalf("loadDeployConfig: %v", err)
	}

	// Collect product names — mirrors what --all does internally
	names := make([]string, 0, len(cfg.Products))
	for name := range cfg.Products {
		names = append(names, name)
	}
	sort.Strings(names)

	wantProducts := []string{"interview-simulator", "study-flow", "voice-coach"}
	for i, name := range names {
		if i >= len(wantProducts) {
			t.Errorf("unexpected extra product: %q", name)
			continue
		}
		if name != wantProducts[i] {
			t.Errorf("product[%d]: got %q, want %q", i, name, wantProducts[i])
		}
	}
	if len(names) != len(wantProducts) {
		t.Errorf("product count: got %d, want %d", len(names), len(wantProducts))
	}
}

func TestDeployCmd_AllFlag_DryRun(t *testing.T) {
	dir := t.TempDir()
	path := writeDeployConfig(t, dir, testDeployYAML)
	t.Setenv("FORGE_DEPLOY_CONFIG", path)

	old := os.Stdout
	r, w, _ := os.Pipe()
	os.Stdout = w

	cmd := deployCmd
	cmd.Flags().Set("all", "true")
	cmd.Flags().Set("dry-run", "true")
	defer func() {
		cmd.Flags().Set("all", "false")
		cmd.Flags().Set("dry-run", "false")
	}()

	runErr := cmd.RunE(cmd, []string{})

	w.Close()
	os.Stdout = old
	buf := make([]byte, 16384)
	n, _ := r.Read(buf)
	output := string(buf[:n])

	if runErr != nil {
		t.Fatalf("unexpected error: %v", runErr)
	}

	// All three products should appear in output
	for _, name := range []string{"interview-simulator", "voice-coach", "study-flow"} {
		if !strings.Contains(output, name) {
			t.Errorf("--all dry-run output missing product %q\nGot:\n%s", name, output)
		}
	}
}

// ---------------------------------------------------------------------------
// shellSplit unit tests
// ---------------------------------------------------------------------------

func TestShellSplit_Simple(t *testing.T) {
	got := shellSplit("alembic upgrade head")
	want := []string{"alembic", "upgrade", "head"}
	if len(got) != len(want) {
		t.Fatalf("shellSplit len: got %v, want %v", got, want)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Errorf("shellSplit[%d]: got %q, want %q", i, got[i], want[i])
		}
	}
}

func TestShellSplit_SingleQuotedArg(t *testing.T) {
	// "npx wrangler pages deploy 'my dir'" → 5 tokens: npx wrangler pages deploy "my dir"
	got := shellSplit("npx wrangler pages deploy 'my dir'")
	if len(got) != 5 {
		t.Fatalf("shellSplit: got %d tokens %v, want 5", len(got), got)
	}
	if got[4] != "my dir" {
		t.Errorf("shellSplit: got %q for quoted arg, want %q", got[4], "my dir")
	}
}

func TestShellSplit_DoubleQuotedArg(t *testing.T) {
	got := shellSplit(`uvicorn "app.main:app" --host 0.0.0.0`)
	if len(got) != 4 {
		t.Fatalf("shellSplit: got %v", got)
	}
	if got[1] != "app.main:app" {
		t.Errorf("shellSplit[1]: got %q, want %q", got[1], "app.main:app")
	}
}

func TestShellSplit_EmptyString(t *testing.T) {
	got := shellSplit("")
	if len(got) != 0 {
		t.Errorf("shellSplit(''): expected empty slice, got %v", got)
	}
}

// ---------------------------------------------------------------------------
// backendSteps / frontendSteps unit tests
// ---------------------------------------------------------------------------

func TestBackendSteps_Railway(t *testing.T) {
	b := &DeployBackend{
		Type:      "railway",
		Directory: "some/dir",
		PreDeploy: "alembic upgrade head",
	}
	steps := backendSteps("myproduct", b)
	if len(steps) != 2 {
		t.Fatalf("expected 2 steps (pre-deploy + railway up), got %d", len(steps))
	}
	if steps[0].cmdArgs[0] != "alembic" {
		t.Errorf("step[0] command: got %q, want %q", steps[0].cmdArgs[0], "alembic")
	}
	if steps[1].cmdArgs[0] != "railway" {
		t.Errorf("step[1] command: got %q, want %q", steps[1].cmdArgs[0], "railway")
	}
}

func TestBackendSteps_UnknownType(t *testing.T) {
	b := &DeployBackend{
		Type:      "heroku",
		Directory: "some/dir",
	}
	steps := backendSteps("myproduct", b)
	// No pre-deploy, one unknown step
	if len(steps) != 1 {
		t.Fatalf("expected 1 step, got %d", len(steps))
	}
	// Unknown type step has no cmdArgs — it's informational only
	if len(steps[0].cmdArgs) != 0 {
		t.Errorf("unknown backend type should produce no cmdArgs, got %v", steps[0].cmdArgs)
	}
}

func TestFrontendSteps_CloudflarePages(t *testing.T) {
	f := &DeployFrontend{
		Type:         "cloudflare-pages",
		Directory:    "some/frontend",
		BuildCommand: "npm run build",
		OutputDir:    "dist",
		ProjectName:  "my-project",
	}
	steps := frontendSteps("myproduct", f)
	if len(steps) != 2 {
		t.Fatalf("expected 2 steps (build + deploy), got %d", len(steps))
	}
	if steps[0].cmdArgs[0] != "npm" {
		t.Errorf("step[0]: want npm, got %q", steps[0].cmdArgs[0])
	}
	// step[1] is: npx wrangler pages deploy dist --project-name my-project
	if len(steps[1].cmdArgs) < 5 {
		t.Fatalf("step[1] too short: %v", steps[1].cmdArgs)
	}
	if steps[1].cmdArgs[0] != "npx" {
		t.Errorf("step[1] command: got %q, want %q", steps[1].cmdArgs[0], "npx")
	}
	if steps[1].cmdArgs[len(steps[1].cmdArgs)-1] != "my-project" {
		t.Errorf("step[1]: expected project name as last arg, got %v", steps[1].cmdArgs)
	}
}

func TestFrontendSteps_DefaultOutputDir(t *testing.T) {
	f := &DeployFrontend{
		Type:      "cloudflare-pages",
		Directory: "some/frontend",
		// No OutputDir — should default to "dist"
	}
	steps := frontendSteps("myproduct", f)
	// No build command → only 1 step
	if len(steps) != 1 {
		t.Fatalf("expected 1 step, got %d: %v", len(steps), steps)
	}
	found := false
	for _, arg := range steps[0].cmdArgs {
		if arg == "dist" {
			found = true
			break
		}
	}
	if !found {
		t.Errorf("expected 'dist' as default output_dir in args: %v", steps[0].cmdArgs)
	}
}
