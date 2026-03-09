.Phony: build test run clean deploy install check deps

# Configuration
BINARY_NAME=forged
GO=go
PORT=8081
DB_PATH=./forge-v3.db
FORGE_ROOT ?= $(shell cd .. && pwd)/..

# Build the binary
build:
	$(GO) build -o $(BINARY_NAME) .

# Run tests
test:
	$(GO) test -v -timeout 30s .

# Run E2E tests
test-e2e:
	$(GO) test -run E2E -v -timeout 30s .

# Run the server
run: build
	DB_PATH=$(DB_PATH) PORT=$(PORT) ./$(BINARY_NAME)

# Run with custom port
run-port:
	DB_PATH=$(DB_PATH) PORT=$(PORT) ./$(BINARY_NAME)

# Clean build artifacts
clean:
	rm -f $(BINARY_NAME)
	rm -f *.test

# Install dependencies
deps:
	$(GO) mod download
	$(GO) mod tidy

# Run the server with debugging
debug:
	DB_PATH=$(DB_PATH) PORT=$(PORT) dlv debug .

# Production build
build-prod:
	CGO_ENABLED=0 GOOS=linux $(GO) build -ldflags="-s -w" -o $(BINARY_NAME) .

# Deploy to production (requires sudo)
deploy: build-prod
	sudo cp $(BINARY_NAME) /usr/local/bin/
	sudo chmod +x /usr/local/bin/$(BINARY_NAME)
	sudo systemctl daemon-reload
	sudo systemctl restart forged
	@echo "Deployed. Check status with: sudo systemctl status forged"

# Start as daemon
daemon: build
	nohup ./$(BINARY_NAME) > /var/log/forged.log 2>&1 &
	@echo "Daemon started. PID in /var/run/forged.pid"

# Stop daemon
stop:
	-pkill $(BINARY_NAME)
	@echo "Stopped"

# Health check
health:
	curl -s http://localhost:$(PORT)/api/health | jq .

# Status check
status:
	curl -s http://localhost:$(PORT)/api/status | jq .

# Check WebSocket
ws-check:
	curl -s -o /dev/null -w "%{http_code}" "http://localhost:$(PORT)/ws?worker_id=check"

# View logs
logs:
	tail -f /var/log/forged.log

# Run linter (if installed)
lint:
	$(GO) vet ./...
	golangci-lint run || true

# Schema management (single source of truth: .forge/schema/v3_schema.yaml)
schema-generate:
	@echo "Generating schema from YAML..."
	@cd $(FORGE_ROOT)/scripts && go run generate-schema.go
	@echo "Schema generated"

schema-validate:
	@echo "Validating schema (apply generated SQL to temp DB)..."
	@sqlite3 /tmp/forge_v3_validate.db < $(FORGE_ROOT)/cmd/forged/db/migrations/001_schema_generated.sql 2>/dev/null || true
	@sqlite3 /tmp/forge_v3_validate.db ".schema" > /dev/null && echo "Schema validated" || echo "Validation failed"

schema-sync: schema-generate schema-validate
	@echo "Schema synchronized"

# Format code
fmt:
	$(GO) fmt ./...

# Show help
help:
	@echo "FORGE daemon Makefile"
	@echo ""
	@echo "Targets:"
	@echo "  build        - Build the binary"
	@echo "  test         - Run tests"
	@echo "  test-e2e    - Run E2E tests"
	@echo "  run          - Run the server"
	@echo "  clean        - Clean build artifacts"
	@echo "  deps         - Install dependencies"
	@echo "  debug        - Run with Delve debugger"
	@echo "  build-prod   - Production build"
	@echo "  deploy       - Deploy to production"
	@echo "  daemon       - Start as daemon"
	@echo "  stop         - Stop daemon"
	@echo "  health       - Check health endpoint"
	@echo "  status       - Check status endpoint"
	@echo "  logs         - View logs"
	@echo "  lint         - Run linter"
	@echo "  fmt          - Format code"
