//go:build !tmux_bridge

// scaffold.go — forge scaffold: generate new service skeletons pre-wired with forge-shared imports.
// Closes Rails8 Pattern 6 (Convention over Config) gap from TC-TECHSTACK-S150.
package main

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/spf13/cobra"
)

// ---------------------------------------------------------------------------
// Supported stacks
// ---------------------------------------------------------------------------

var validScaffoldStacks = []string{"fastapi", "phoenix", "go-htmx"}

// scaffoldFile represents a file to be created by the scaffold command.
type scaffoldFile struct {
	// path is relative to the service root (e.g. "backend/app/core/config.py")
	path    string
	content string
}

// ---------------------------------------------------------------------------
// Template generators — one func per stack
// ---------------------------------------------------------------------------

func fastAPIFiles(name, domain string) []scaffoldFile {
	domainComment := ""
	if domain != "" {
		domainComment = fmt.Sprintf("# domain: %s\n", domain)
	}

	configPy := domainComment + `from pydantic_settings import BaseSettings

from forge_shared.auth import ForgeAuth
from forge_shared.jobs.queue import JobQueue


class Settings(BaseSettings):
    database_url: str = ""
    port: int = 8000
    forge_auth: ForgeAuth = ForgeAuth()
    job_queue: JobQueue = JobQueue()

    class Config:
        env_file = ".env"


settings = Settings()
`

	mainPy := fmt.Sprintf(`from fastapi import FastAPI

app = FastAPI(title="%s")


@app.get("/health")
def health():
    return {"status": "ok", "service": "%s"}
`, name, name)

	pyprojectToml := fmt.Sprintf(`[project]
name = "%s"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.110.0",
    "uvicorn[standard]>=0.29.0",
    "pydantic-settings>=2.2.0",
    "alembic>=1.13.0",
    "sqlalchemy>=2.0.0",
    "forge-shared>=0.1.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["app"]
`, name)

	dockerfile := `FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir -e .

COPY . .
RUN chmod +x entrypoint.sh

ENTRYPOINT ["./entrypoint.sh"]
`

	entrypointSh := `#!/bin/bash
set -e
echo "=== Running migrations ==="
alembic upgrade head
echo "=== Starting server ==="
exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
`

	alembicIni := fmt.Sprintf(`[alembic]
script_location = alembic
prepend_sys_path = .
sqlalchemy.url = ${DATABASE_URL}

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console
qualname =

[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %%(levelname)-5.5s [%%(name)s] %%(message)s
datefmt = %%%%(asctime)s
# service: %s
`, name)

	railwayToml := fmt.Sprintf(`[deploy]
startCommand = "./entrypoint.sh"
builder = "DOCKERFILE"
dockerfilePath = "Dockerfile"
healthcheckPath = "/health"
healthcheckTimeout = 30

[deploy.envVars]
SERVICE_NAME = "%s"
`, name)

	return []scaffoldFile{
		{path: "backend/app/api/routes/.gitkeep", content: ""},
		{path: "backend/app/core/config.py", content: configPy},
		{path: "backend/app/main.py", content: mainPy},
		{path: "backend/app/models/.gitkeep", content: ""},
		{path: "backend/alembic/versions/.gitkeep", content: ""},
		{path: "backend/alembic.ini", content: alembicIni},
		{path: "backend/pyproject.toml", content: pyprojectToml},
		{path: "backend/Dockerfile", content: dockerfile},
		{path: "backend/entrypoint.sh", content: entrypointSh},
		{path: "backend/railway.toml", content: railwayToml},
	}
}

func phoenixFiles(name, domain string) []scaffoldFile {
	appModule := toCamelCase(name)

	mixExs := fmt.Sprintf(`defmodule %s.MixProject do
  use Mix.Project

  def project do
    [
      app: :%s,
      version: "0.1.0",
      elixir: "~> 1.16",
      elixirc_paths: elixirc_paths(Mix.env()),
      start_permanent: Mix.env() == :prod,
      deps: deps()
    ]
  end

  def application do
    [
      mod: {%s.Application, []},
      extra_applications: [:logger, :runtime_tools]
    ]
  end

  defp elixirc_paths(:test), do: ["lib", "test/support"]
  defp elixirc_paths(_), do: ["lib"]

  defp deps do
    [
      {:phoenix, "~> 1.7"},
      {:phoenix_live_view, "~> 0.20"},
      {:oban, "~> 2.17"},
      {:ecto_sql, "~> 3.11"},
      {:postgrex, ">= 0.0.0"},
      {:phoenix_html, "~> 4.0"},
      {:telemetry_metrics, "~> 0.6"},
      {:telemetry_poller, "~> 1.0"},
      {:jason, "~> 1.2"},
      {:plug_cowboy, "~> 2.5"}
    ]
  end
end
`, appModule, name, appModule)

	configExs := fmt.Sprintf(`import Config

config :%s,
  ecto_repos: [%s.Repo]

config :%s, %sWeb.Endpoint,
  url: [host: "localhost"],
  adapter: Phoenix.Endpoint.Cowboy2Adapter,
  render_errors: [formats: [html: %sWeb.ErrorHTML, json: %sWeb.ErrorJSON], layout: false],
  pubsub_server: %s.PubSub,
  live_view: [signing_salt: "forge_salt"]

config :logger, :console,
  format: "$time $metadata[$level] $message\n",
  metadata: [:request_id]

config :phoenix, :json_library, Jason

import_config "#{config_env()}.exs"
`, name, appModule, name, appModule, appModule, appModule, appModule)

	runtimeExs := fmt.Sprintf(`import Config

if config_env() == :prod do
  database_url =
    System.get_env("DATABASE_URL") ||
      raise "environment variable DATABASE_URL is missing."

  config :%s, %s.Repo,
    url: database_url,
    pool_size: String.to_integer(System.get_env("POOL_SIZE") || "10")

  config :%s, %sWeb.Endpoint,
    url: [host: System.get_env("PHX_HOST") || "example.com", port: 443, scheme: "https"],
    http: [
      ip: {0, 0, 0, 0, 0, 0, 0, 0},
      port: String.to_integer(System.get_env("PORT") || "4000")
    ],
    secret_key_base: System.get_env("SECRET_KEY_BASE") ||
      raise("environment variable SECRET_KEY_BASE is missing.")
end
`, name, appModule, name, appModule)

	routerEx := fmt.Sprintf(`defmodule %sWeb.Router do
  use %sWeb, :router

  pipeline :api do
    plug :accepts, ["json"]
  end

  pipeline :browser do
    plug :accepts, ["html"]
    plug :fetch_session
    plug :protect_from_forgery
    plug :put_secure_browser_headers
  end

  scope "/api", %sWeb do
    pipe_through :api

    get "/health", HealthController, :index
  end

  scope "/", %sWeb do
    pipe_through :browser

    live "/", HomeLive, :index
  end
end
`, appModule, appModule, appModule, appModule)

	railwayToml := fmt.Sprintf(`[deploy]
startCommand = "mix phx.server"
healthcheckPath = "/api/health"
healthcheckTimeout = 30

[deploy.envVars]
SERVICE_NAME = "%s"
MIX_ENV = "prod"
`, name)

	domainLine := ""
	if domain != "" {
		domainLine = fmt.Sprintf("# domain: %s\n", domain)
	}

	return []scaffoldFile{
		{path: "mix.exs", content: domainLine + mixExs},
		{path: "config/config.exs", content: configExs},
		{path: "config/runtime.exs", content: runtimeExs},
		{path: fmt.Sprintf("lib/%s_web/router.ex", name), content: routerEx},
		{path: fmt.Sprintf("lib/%s_web/live/.gitkeep", name), content: ""},
		{path: "railway.toml", content: railwayToml},
	}
}

func goHTMXFiles(name, domain string) []scaffoldFile {
	domainComment := ""
	if domain != "" {
		domainComment = "// domain: " + domain + "\n"
	}

	// Use strings.ReplaceAll instead of fmt.Sprintf to avoid escaping issues
	// with % characters that appear in the generated Go source code.
	const mainGoTmpl = `package main

import (
	"fmt"
	"log"
	"net/http"
	"os"
)

func main() {
	mux := http.NewServeMux()

	mux.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprintf(w, `+"`"+`{"status":"ok","service":"SERVICE_NAME"}`+"`"+`)
	})

	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		fmt.Fprintf(w, "<html><body><h1>SERVICE_NAME</h1></body></html>")
	})

	port := os.Getenv("PORT")
	if port == "" {
		port = "8080"
	}

	log.Printf("Starting SERVICE_NAME on :%s", port)
	if err := http.ListenAndServe(":"+port, mux); err != nil {
		log.Fatalf("server error: %v", err)
	}
}
`
	mainGo := domainComment + strings.ReplaceAll(mainGoTmpl, "SERVICE_NAME", name)

	dockerfile := "FROM golang:1.22-alpine AS builder\n" +
		"WORKDIR /app\n" +
		"COPY . .\n" +
		"RUN go build -o server .\n\n" +
		"FROM alpine:3.19\n" +
		"WORKDIR /app\n" +
		"COPY --from=builder /app/server .\n" +
		"EXPOSE 8080\n" +
		"CMD [\"./server\"]\n" +
		"# service: " + name + "\n"

	railwayToml := "[deploy]\n" +
		"startCommand = \"./server\"\n" +
		"builder = \"DOCKERFILE\"\n" +
		"dockerfilePath = \"Dockerfile\"\n" +
		"healthcheckPath = \"/health\"\n" +
		"healthcheckTimeout = 30\n\n" +
		"[deploy.envVars]\n" +
		"SERVICE_NAME = \"" + name + "\"\n"

	return []scaffoldFile{
		{path: "main.go", content: mainGo},
		{path: "Dockerfile", content: dockerfile},
		{path: "railway.toml", content: railwayToml},
	}
}

// ---------------------------------------------------------------------------
// Helper: convert kebab-case / snake_case name to CamelCase for Elixir modules
// ---------------------------------------------------------------------------

func toCamelCase(s string) string {
	parts := strings.FieldsFunc(s, func(r rune) bool {
		return r == '-' || r == '_'
	})
	var b strings.Builder
	for _, p := range parts {
		if len(p) == 0 {
			continue
		}
		b.WriteString(strings.ToUpper(p[:1]))
		b.WriteString(p[1:])
	}
	return b.String()
}

// ---------------------------------------------------------------------------
// Cobra command
// ---------------------------------------------------------------------------

var scaffoldCmd = &cobra.Command{
	Use:   "scaffold <stack> <name>",
	Short: "Generate a new service skeleton pre-wired with forge-shared imports",
	Long: `Generate a new service skeleton under services/<name>/ pre-wired with
forge-shared imports and Railway deployment configuration.

Supported stacks:
  fastapi    FastAPI + PostgreSQL service
  phoenix    Elixir Phoenix/LiveView service
  go-htmx    Go + HTMX service

Examples:
  forge scaffold fastapi my-api --domain vc
  forge scaffold phoenix my-app
  forge scaffold go-htmx my-service --dry-run`,
	Args: cobra.ExactArgs(2),
	ValidArgsFunction: func(cmd *cobra.Command, args []string, toComplete string) ([]string, cobra.ShellCompDirective) {
		if len(args) == 0 {
			return validScaffoldStacks, cobra.ShellCompDirectiveNoFileComp
		}
		return nil, cobra.ShellCompDirectiveDefault
	},
	RunE: func(cmd *cobra.Command, args []string) error {
		stack := args[0]
		name := args[1]
		domain, _ := cmd.Flags().GetString("domain")
		dryRun, _ := cmd.Flags().GetBool("dry-run")

		// Validate stack
		validStack := false
		for _, s := range validScaffoldStacks {
			if s == stack {
				validStack = true
				break
			}
		}
		if !validStack {
			return fmt.Errorf("unknown stack %q\n  Valid stacks: %s\n  Fix: choose one of the supported stacks above",
				stack, strings.Join(validScaffoldStacks, ", "))
		}

		// Validate name
		if strings.TrimSpace(name) == "" {
			return fmt.Errorf("service name cannot be empty")
		}

		// Generate file list for the chosen stack
		var files []scaffoldFile
		switch stack {
		case "fastapi":
			files = fastAPIFiles(name, domain)
		case "phoenix":
			files = phoenixFiles(name, domain)
		case "go-htmx":
			files = goHTMXFiles(name, domain)
		}

		serviceRoot := filepath.Join("services", name)

		if dryRun {
			fmt.Printf("[dry-run] Would create service skeleton at %s/\n\n", serviceRoot)
			fmt.Printf("  Stack:  %s\n", stack)
			if domain != "" {
				fmt.Printf("  Domain: %s\n", domain)
			}
			fmt.Println("\n  Files:")
			for _, f := range files {
				fmt.Printf("    %s\n", filepath.Join(serviceRoot, f.path))
			}
			fmt.Printf("\nTotal: %d files\n", len(files))
			return nil
		}

		// Check that services/<name> does not already exist
		if _, err := os.Stat(serviceRoot); err == nil {
			return fmt.Errorf("directory %q already exists\n  Fix: choose a different name or remove the existing directory first", serviceRoot)
		}

		// Write files
		created := 0
		for _, f := range files {
			fullPath := filepath.Join(serviceRoot, f.path)
			dir := filepath.Dir(fullPath)

			if err := os.MkdirAll(dir, 0755); err != nil {
				return fmt.Errorf("failed to create directory %q: %w\n  Fix: check permissions on %s", dir, err, serviceRoot)
			}

			if err := os.WriteFile(fullPath, []byte(f.content), 0644); err != nil {
				return fmt.Errorf("failed to write %q: %w", fullPath, err)
			}

			// Mark entrypoint.sh executable
			if strings.HasSuffix(f.path, "entrypoint.sh") {
				if err := os.Chmod(fullPath, 0755); err != nil {
					fmt.Fprintf(os.Stderr, "warning: could not chmod +x %s: %v\n", fullPath, err)
				}
			}

			fmt.Printf("  created  %s\n", fullPath)
			created++
		}

		fmt.Printf("\nScaffolded %s service %q (%d files) at %s/\n", stack, name, created, serviceRoot)
		if domain != "" {
			fmt.Printf("Domain:  %s\n", domain)
		}
		fmt.Printf("\nNext steps:\n")
		switch stack {
		case "fastapi":
			fmt.Printf("  cd %s/backend\n", serviceRoot)
			fmt.Printf("  uv sync\n")
			fmt.Printf("  railway up\n")
		case "phoenix":
			fmt.Printf("  cd %s\n", serviceRoot)
			fmt.Printf("  mix deps.get\n")
			fmt.Printf("  mix phx.server\n")
		case "go-htmx":
			fmt.Printf("  cd %s\n", serviceRoot)
			fmt.Printf("  go run .\n")
			fmt.Printf("  railway up\n")
		}

		return nil
	},
}

func init() {
	scaffoldCmd.Flags().String("domain", "", "Domain shortcode (e.g. vc, cs, cc)")
	scaffoldCmd.Flags().Bool("dry-run", false, "Print what would be created without writing files")
}
