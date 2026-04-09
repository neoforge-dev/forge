# FORGE Portfolio - Local Development
# ===================================
#
# Quick Start:
#   make setup      # One-time setup
#   make interview  # Start Interview Simulator

COMPOSE := docker compose -f docker-compose.local.yml

.PHONY: help setup infra proxy down stop logs status \
        interview atlas diligence studyflow codeship storygrow voicecoach \
        migrate local-setup intake-mvp schema-generate schema-validate schema-sync

# =============================================================================
# HELP
# =============================================================================

help:
	@echo "FORGE Local Development"
	@echo ""
	@echo "Setup:"
	@echo "  make setup        One-time setup (env + infra)"
	@echo "  make local-setup  Configure .local domains (requires sudo)"
	@echo ""
	@echo "Products:"
	@echo "  make interview    Interview Simulator (port 8000, 5173)"
	@echo "  make voicecoach   Voice Coach (port 8008, 5180)"
	@echo "  make atlas        Code Atlas (port 8002)"
	@echo "  make diligence    Tech Diligence (port 8003)"
	@echo "  make studyflow    Study Flow (port 8013)"
	@echo "  make codeship     Code Ship (port 8012)"
	@echo "  make storygrow    Story Grow (port 8011)"
	@echo ""
	@echo "Control:"
	@echo "  make stop         Stop all services"
	@echo "  make logs         Tail all logs"
	@echo "  make status       Show running services"
	@echo "  make migrate      Run all migrations"
	@echo "  make proxy        Start Caddy (auto-started by products)"
	@echo ""
	@echo "Intake:"
	@echo "  make intake-mvp   Import external projects (run for help)"
	@echo ""
	@echo "Schema (v3):"
	@echo "  make schema-generate   Generate SQL from .forge/schema/v3_schema.yaml"
	@echo "  make schema-validate   Validate generated schema with SQLite"
	@echo "  make schema-sync       Generate + validate"
	@echo ""
	@echo "Ports: 8000-8013 (APIs), 5173-5180 (frontends)"

# =============================================================================
# SETUP (one-time)
# =============================================================================

setup:
	@if [ ! -f .env.local ]; then \
		cp .env.local.example .env.local 2>/dev/null || echo "# Add API keys" > .env.local; \
		echo "Created .env.local - add your API keys"; \
	fi
	@$(COMPOSE) up -d postgres redis
	@echo "Waiting for PostgreSQL..."
	@sleep 3
	@echo "Setup complete. Run: make interview"

local-setup:
	@echo "Setting up .local domains (requires sudo)..."
	@grep -q "codeswiftr.local" /etc/hosts || \
		echo "127.0.0.1 codeswiftr.local app.codeswiftr.local api.codeswiftr.local" | sudo tee -a /etc/hosts
	@echo "Local domains configured."

# =============================================================================
# INFRASTRUCTURE
# =============================================================================

infra:
	@$(COMPOSE) up -d postgres redis 2>/dev/null || true

proxy:
	@# Start Caddy if local domains are configured and Caddy is installed
	@if grep -q "codeswiftr.local" /etc/hosts 2>/dev/null && which caddy >/dev/null 2>&1; then \
		if ! pgrep -x caddy >/dev/null 2>&1; then \
			echo "Starting Caddy reverse proxy..."; \
			caddy start --config Caddyfile.local 2>/dev/null || true; \
			sleep 1; \
		fi \
	fi

down:
	@$(COMPOSE) down 2>/dev/null || true
	@pkill -f "uvicorn" 2>/dev/null || true
	@pkill -f "vite" 2>/dev/null || true
	@pkill caddy 2>/dev/null || true
	@echo "All services stopped."

stop: down

# =============================================================================
# INTERVIEW SIMULATOR (main product)
# =============================================================================

interview: infra proxy
	@echo "Starting Interview Simulator..."
	@cd codeswiftr-com/interview-simulator && docker compose up -d 2>/dev/null || true
	@sleep 1
	@cd marketing-api && (uv run uvicorn app.main:app --reload --port 8001 > /tmp/forge-core.log 2>&1 &)
	@cd codeswiftr-com/interview-simulator/backend && (uv run uvicorn app.main:app --reload --port 8000 > /tmp/interview-api.log 2>&1 &)
	@sleep 1
	@cd codeswiftr-com/interview-simulator/frontend && (npm run dev > /tmp/interview-frontend.log 2>&1 &)
	@cd marketing-template && (VITE_LANDING_DOMAIN=codeswiftr npm run dev -- --port 5174 > /tmp/marketing-frontend.log 2>&1 &)
	@sleep 3
	@$(MAKE) -s _open-interview
	@echo ""
	@echo "Interview Simulator running:"
	@if grep -q "codeswiftr.local" /etc/hosts 2>/dev/null && pgrep -x caddy >/dev/null 2>&1; then \
		echo "  App:       http://app.codeswiftr.local:8080"; \
		echo "  Marketing: http://codeswiftr.local:8080"; \
		echo "  API:       http://interview-api.codeswiftr.local:8080"; \
		echo "  Core API:  http://api.codeswiftr.local:8080"; \
	else \
		echo "  App:       http://localhost:5173"; \
		echo "  Marketing: http://localhost:5174"; \
		echo "  API:       http://localhost:8000"; \
	fi
	@echo ""
	@echo "Logs: tail -f /tmp/interview-*.log /tmp/forge-core.log"

_open-interview:
	@if grep -q "codeswiftr.local" /etc/hosts 2>/dev/null && pgrep -x caddy >/dev/null 2>&1; then \
		open -a "Google Chrome" "http://app.codeswiftr.local:8080" "http://codeswiftr.local:8080" 2>/dev/null || true; \
	else \
		open -a "Google Chrome" "http://localhost:5173" "http://localhost:5174" 2>/dev/null || true; \
	fi

# =============================================================================
# VOICE COACH (brandfocus.ai)
# =============================================================================

voicecoach: infra proxy
	@echo "Starting Voice Coach + FORGE Core..."
	@# Start Voice Coach infra (LocalStack for S3)
	@cd brandfocus-ai/voice-coach/app && docker compose up -d 2>/dev/null || true
	@sleep 1
	@# Start FORGE Core (auth service) - required for login
	@cd marketing-api && (uv run uvicorn app.main:app --reload --port 8000 > /tmp/forge-core.log 2>&1 &)
	@sleep 1
	@# Run Voice Coach migrations
	@cd brandfocus-ai/voice-coach/app/backend && uv run alembic upgrade head 2>/dev/null || true
	@# Start Voice Coach API
	@cd brandfocus-ai/voice-coach/app/backend && (uv run uvicorn app.main:app --reload --port 8008 > /tmp/voicecoach-api.log 2>&1 &)
	@sleep 1
	@cd brandfocus-ai/voice-coach/app/frontend && (npm run dev -- --port 5180 > /tmp/voicecoach-frontend.log 2>&1 &)
	@sleep 3
	@open -a "Google Chrome" "http://localhost:5180" 2>/dev/null || true
	@echo ""
	@echo "Voice Coach running:"
	@echo "  App:        http://localhost:5180"
	@echo "  API:        http://localhost:8008"
	@echo "  API Docs:   http://localhost:8008/docs"
	@echo "  FORGE Core: http://localhost:8000 (auth)"
	@echo ""
	@echo "Logs: tail -f /tmp/voicecoach-*.log /tmp/forge-core.log"

# =============================================================================
# OTHER PRODUCTS
# =============================================================================

atlas: infra
	@echo "Starting Code Atlas..."
	@cd codeswiftr-com/code-atlas/backend && (uv run uvicorn app.main:app --reload --port 8002 > /tmp/atlas-api.log 2>&1 &)
	@cd codeswiftr-com/code-atlas/web && (npm run dev -- --port 5175 > /tmp/atlas-frontend.log 2>&1 &)
	@sleep 2
	@open -a "Google Chrome" "http://localhost:5175" 2>/dev/null || true
	@echo "Code Atlas: http://localhost:5175 (API: 8002)"

diligence: infra
	@echo "Starting Tech Diligence..."
	@cd codeswiftr-com/tech-diligence-snapshot/backend && (uv run uvicorn app.main:app --reload --port 8003 > /tmp/diligence-api.log 2>&1 &)
	@cd codeswiftr-com/tech-diligence-snapshot/frontend && (npm run dev -- --port 5176 > /tmp/diligence-frontend.log 2>&1 &)
	@sleep 2
	@open -a "Google Chrome" "http://localhost:5176" 2>/dev/null || true
	@echo "Tech Diligence: http://localhost:5176 (API: 8003)"

studyflow: infra
	@echo "Starting Study Flow..."
	@cd thebrightharbor-com/study-flow/backend && (uv run uvicorn app.main:app --reload --port 8013 > /tmp/studyflow-api.log 2>&1 &)
	@cd thebrightharbor-com/study-flow/frontend && (npm run dev -- --port 5177 > /tmp/studyflow-frontend.log 2>&1 &)
	@sleep 2
	@open -a "Google Chrome" "http://localhost:5177" 2>/dev/null || true
	@echo "Study Flow: http://localhost:5177 (API: 8013)"

codeship: infra
	@echo "Starting Code Ship..."
	@cd thebrightharbor-com/code-ship/backend && (uv run uvicorn app.main:app --reload --port 8012 > /tmp/codeship-api.log 2>&1 &)
	@cd thebrightharbor-com/code-ship/frontend && (npm run dev -- --port 5178 > /tmp/codeship-frontend.log 2>&1 &)
	@sleep 2
	@open -a "Google Chrome" "http://localhost:5178" 2>/dev/null || true
	@echo "Code Ship: http://localhost:5178 (API: 8012)"

storygrow: infra
	@echo "Starting Story Grow..."
	@cd thebrightharbor-com/story-grow/backend && (uv run uvicorn app.main:app --reload --port 8011 > /tmp/storygrow-api.log 2>&1 &)
	@cd thebrightharbor-com/story-grow/frontend && (npm run dev -- --port 5179 > /tmp/storygrow-frontend.log 2>&1 &)
	@sleep 2
	@open -a "Google Chrome" "http://localhost:5179" 2>/dev/null || true
	@echo "Story Grow: http://localhost:5179 (API: 8011)"

# =============================================================================
# V3 SERVER DEPLOYMENT
# =============================================================================

# Deployment
deploy:
	@chmod +x scripts/deploy-v3.sh
	@./scripts/deploy-v3.sh

# Quick restart (no build)
restart-v3:
	@echo "🔄 Restarting v3 server..."
	@./scripts/deploy-v3.sh

# Check v3 status
status-v3:
	@echo "📊 v3 Server Status:"
	@if [ -f /tmp/forge-v3.pid ] && kill -0 $$(cat /tmp/forge-v3.pid) 2>/dev/null; then \
		echo "  ✅ Running (PID: $$(cat /tmp/forge-v3.pid))"; \
		curl -s http://localhost:8081/health | jq -r '"  Health: " + .status' 2>/dev/null || echo "  ⚠️  Health check failed"; \
	else \
		echo "  ❌ Not running"; \
	fi

# =============================================================================
# UTILITIES
# =============================================================================

logs:
	@tail -f /tmp/*.log 2>/dev/null || echo "No logs found. Start a service first."

status:
	@echo "Running services:"
	@echo ""
	@lsof -i :8000-8013 -i :5173-5180 2>/dev/null | grep LISTEN | awk '{print "  " $$1 " on port " $$9}' || echo "  None"
	@echo ""
	@$(COMPOSE) ps --format "table {{.Name}}\t{{.Status}}" 2>/dev/null || true

migrate: infra
	@echo "Running migrations..."
	@cd codeswiftr-com/interview-simulator/backend && uv run alembic upgrade head 2>/dev/null || true
	@cd brandfocus-ai/voice-coach/app/backend && uv run alembic upgrade head 2>/dev/null || true
	@cd codeswiftr-com/code-atlas/backend && uv run alembic upgrade head 2>/dev/null || true
	@cd thebrightharbor-com/study-flow/backend && uv run alembic upgrade head 2>/dev/null || true
	@cd thebrightharbor-com/code-ship/backend && uv run alembic upgrade head 2>/dev/null || true
	@echo "Migrations complete."

# =============================================================================
# MVP INTAKE
# =============================================================================

intake-mvp:
	@if [ -z "$(SRC)" ] && [ -z "$(SPEC)" ]; then \
		echo "MVP Intake - import projects or create from spec"; \
		echo ""; \
		echo "From existing project:"; \
		echo "  make intake-mvp SRC=../path/to/mvp                    # Analyze only"; \
		echo "  make intake-mvp SRC=../path/to/mvp MODE=auto          # Auto-detect domain"; \
		echo "  make intake-mvp SRC=../path/to/mvp MODE=auto LANDING=1  # + landing page"; \
		echo ""; \
		echo "From spec files (new project):"; \
		echo "  make intake-mvp SPEC=docs/idea.md PROJECT=my-app"; \
		echo "  make intake-mvp SPEC=\"spec1.md spec2.md\" PROJECT=cool-app DOMAIN=codeswiftr-com"; \
		echo "  make intake-mvp SPEC=specs/ PROJECT=new-mvp LANDING=1 BACKEND=1 FRONTEND=1"; \
		echo ""; \
		echo "Options:"; \
		echo "  SRC=<path>       Source directory (existing project)"; \
		echo "  SPEC=<files>     Spec file(s) or directory (new project)"; \
		echo "  MODE=analyze     Just analyze (default for SRC)"; \
		echo "  MODE=auto        Auto-detect domain and scaffold"; \
		echo "  MODE=scaffold    Manual scaffold (requires DOMAIN, PROJECT)"; \
		echo "  DOMAIN=<domain>  Target domain (e.g., codeswiftr-com)"; \
		echo "  PROJECT=<slug>   Project slug (required for SPEC)"; \
		echo "  IMPORT=copy      Copy files into FORGE"; \
		echo "  IMPORT=submodule Add as git submodule"; \
		echo "  LANDING=1        Add landing page stub"; \
		echo "  BACKEND=1        Scaffold FastAPI backend"; \
		echo "  FRONTEND=1       Scaffold React frontend"; \
		echo "  NOLLM=1          Skip LLM, use keyword heuristic"; \
		echo "  YES=1            Skip confirmations"; \
	elif [ -n "$(SPEC)" ]; then \
		[ -z "$(PROJECT)" ] && echo "ERROR: PROJECT required for spec mode" && exit 1; \
		ARGS=""; \
		[ -n "$(DOMAIN)" ] && ARGS="$$ARGS --domain $(DOMAIN)"; \
		[ -n "$(LANDING)" ] && ARGS="$$ARGS --landing"; \
		[ -n "$(BACKEND)" ] && ARGS="$$ARGS --backend"; \
		[ -n "$(FRONTEND)" ] && ARGS="$$ARGS --frontend"; \
		[ -n "$(NOLLM)" ] && ARGS="$$ARGS --no-llm"; \
		./scripts/intake-mvp.sh spec $(SPEC) --project $(PROJECT) $$ARGS; \
	else \
		MODE=$${MODE:-analyze}; \
		case "$$MODE" in \
			analyze) \
				./scripts/intake-mvp.sh analyze "$(SRC)" ;; \
			auto) \
				ARGS=""; \
				[ -n "$(DOMAIN)" ] && ARGS="$$ARGS --domain $(DOMAIN)"; \
				[ -n "$(PROJECT)" ] && ARGS="$$ARGS --project $(PROJECT)"; \
				[ -n "$(IMPORT)" ] && ARGS="$$ARGS --import $(IMPORT)"; \
				[ -n "$(LANDING)" ] && ARGS="$$ARGS --landing"; \
				[ -n "$(YES)" ] && ARGS="$$ARGS --yes"; \
				./scripts/intake-mvp.sh auto "$(SRC)" $$ARGS ;; \
			scaffold) \
				[ -z "$(DOMAIN)" ] && echo "ERROR: DOMAIN required for scaffold mode" && exit 1; \
				[ -z "$(PROJECT)" ] && echo "ERROR: PROJECT required for scaffold mode" && exit 1; \
				ARGS="--domain $(DOMAIN) --project $(PROJECT)"; \
				[ -n "$(IMPORT)" ] && ARGS="$$ARGS --import $(IMPORT)"; \
				[ -n "$(YES)" ] && ARGS="$$ARGS --yes"; \
				./scripts/intake-mvp.sh scaffold "$(SRC)" $$ARGS ;; \
			*) \
				echo "ERROR: Unknown MODE=$$MODE (use analyze, auto, or scaffold)" && exit 1 ;; \
		esac \
	fi

# =============================================================================
# GIT HOOKS (Pre-commit checks)
# =============================================================================

install-hooks:
	@echo "Installing git hooks..."
	@chmod +x scripts/install-git-hooks.sh
	@./scripts/install-git-hooks.sh

# Pre-commit check (manual run)
pre-commit-check:
	@echo "Running pre-commit checks..."
	@bash .forge/hooks/pre-commit

# =============================================================================
# INTERFACE-FIRST DEVELOPMENT
# =============================================================================

new-interface:
	@./scripts/generate-interface.sh $(name)

verify-interfaces:
	@echo "Verifying all interfaces..."
	@cd cmd/forge-v3 && go build -o /tmp/verify 2>&1 || \
		(echo "❌ Interface mismatch detected"; exit 1)
	@echo "✅ All interfaces verified"

# =============================================================================
# SCHEMA AS CODE
# =============================================================================

# Schema management
schema-generate:
	@echo "🔄 Generating schema from YAML..."
	@cd scripts && go run generate-schema.go
	@echo "✅ Schema generated"

schema-validate:
	@echo "🔍 Validating schema..."
	@if [ -f cmd/forge-v3/db/migrations/001_schema.sql ]; then \
		sqlite3 .forge/forge-v3.db < cmd/forge-v3/db/migrations/001_schema.sql 2>&1 || true; \
		echo "✅ Schema validated"; \
	else \
		echo "❌ Migration file not found. Run 'make schema-generate' first"; \
	fi

schema-sync: schema-generate schema-validate
	@echo "✅ Schema synchronized"

# =============================================================================
# GO BINARY BUILDS (forge CLI + forged daemon)
# =============================================================================

GOBIN_VERSION   ?= $(shell git describe --tags --always --dirty 2>/dev/null || echo dev)
GOBIN_COMMIT    ?= $(shell git rev-parse --short HEAD 2>/dev/null || echo unknown)
GOBIN_BUILDTIME ?= $(shell date -u +%Y-%m-%dT%H:%M:%SZ)
GOBIN_LDFLAGS    = -X main.Version=$(GOBIN_VERSION) -X main.GitCommit=$(GOBIN_COMMIT) -X main.BuildTime=$(GOBIN_BUILDTIME)

.PHONY: forge-build forge-daemon forge-install forge-all forge-clean

forge-build:           ## Build forge CLI  →  bin/forge
	@mkdir -p bin
	cd cmd/forge && go build -ldflags "$(GOBIN_LDFLAGS)" -o ../../bin/forge .

forge-daemon:          ## Build forged daemon  →  bin/forged
	@mkdir -p bin
	cd cmd/forged && go build -ldflags "$(GOBIN_LDFLAGS)" -o ../../bin/forged .

forge-install: forge-build   ## Build + install forge CLI to ~/.local/bin
	cp bin/forge $(HOME)/.local/bin/forge

forge-all: forge-build forge-daemon ## Build both CLI and daemon

forge-clean:           ## Remove Go build artifacts
	rm -f bin/forge bin/forged

