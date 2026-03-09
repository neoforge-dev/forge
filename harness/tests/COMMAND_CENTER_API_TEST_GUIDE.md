# Command Center API Test Guide

Quick reference for running and maintaining API tests.

## Test Structure

```
tests/
├── test_command_center_api.py          # Unit tests (fast, many)
├── integration/
│   └── test_command_center_integration.py  # Integration tests (medium)
├── test_api_contracts.py               # Contract validation
└── TEST_RESULTS_COMMAND_CENTER_API.md  # Latest test results
```

---

## Quick Commands

### Run All Command Center Tests
```bash
cd /Users/bogdan/work/FORGE/harness
uv run pytest tests/test_command_center_api.py tests/test_api_contracts.py -v
```

### Run Only Passing Tests
```bash
uv run pytest tests/test_command_center_api.py -v -k "not (test_get_agent_by_id or test_register_agent or test_approve_request)"
```

### Run Tests for Specific Endpoint
```bash
# Agents endpoints
uv run pytest tests/test_command_center_api.py::TestAgentsEndpoints -v

# Approvals endpoints
uv run pytest tests/test_command_center_api.py::TestApprovalsEndpoints -v

# Portfolio endpoints
uv run pytest tests/test_command_center_api.py::TestPortfolioEndpoints -v
```

### Watch Mode (Auto-rerun on Changes)
```bash
uv run pytest tests/test_command_center_api.py --watch
```

### With Coverage Report
```bash
uv run pytest tests/test_command_center_api.py \
  --cov=forge_harness.webhook_server \
  --cov-report=html \
  --cov-report=term-missing
```

Then open `htmlcov/index.html` to view coverage report.

---

## Test Categories

### Unit Tests (Fast)
**File**: `test_command_center_api.py`
**Purpose**: Test each endpoint in isolation
**Run Time**: ~2 seconds

```bash
# Run all unit tests
uv run pytest tests/test_command_center_api.py -v

# Run specific test class
uv run pytest tests/test_command_center_api.py::TestHealthEndpoint -v

# Run single test
uv run pytest tests/test_command_center_api.py::TestHealthEndpoint::test_health_check -v
```

### Integration Tests (Medium)
**File**: `integration/test_command_center_integration.py`
**Purpose**: Test full workflows and data consistency
**Run Time**: ~10-15 seconds

```bash
# Run integration tests
uv run pytest tests/integration/test_command_center_integration.py -v

# Test agent lifecycle
uv run pytest tests/integration/test_command_center_integration.py::TestAgentLifecycle -v

# Test approval workflow
uv run pytest tests/integration/test_command_center_integration.py::TestApprovalWorkflow -v
```

### Contract Tests (Validation)
**File**: `test_api_contracts.py`
**Purpose**: Validate response schemas match API_CONTRACTS.md
**Run Time**: ~2 seconds

```bash
# Run contract validation
uv run pytest tests/test_api_contracts.py -v

# Test specific API contract
uv run pytest tests/test_api_contracts.py::TestAgentsAPIContract -v
```

---

## Continuous Integration

### Pre-Commit Hook
Add to `.git/hooks/pre-commit`:
```bash
#!/bin/bash
echo "Running Command Center API tests..."
uv run pytest tests/test_command_center_api.py -q
if [ $? -ne 0 ]; then
  echo "Tests failed! Commit aborted."
  exit 1
fi
```

### CI/CD Pipeline
```yaml
# GitHub Actions example
test-command-center-api:
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@v3
    - name: Set up Python
      uses: actions/setup-python@v4
      with:
        python-version: '3.13'
    - name: Install dependencies
      run: |
        pip install uv
        uv sync
    - name: Run API tests
      run: |
        uv run pytest tests/test_command_center_api.py \
          tests/test_api_contracts.py \
          --cov=forge_harness.webhook_server \
          --cov-report=xml
    - name: Upload coverage
      uses: codecov/codecov-action@v3
```

---

## Debugging Failed Tests

### Show Full Error Output
```bash
uv run pytest tests/test_command_center_api.py -vv --tb=long
```

### Run Failed Tests Only
```bash
uv run pytest tests/test_command_center_api.py --lf -v
```

### Debug with PDB
```bash
uv run pytest tests/test_command_center_api.py --pdb -v
```

### Show Print Statements
```bash
uv run pytest tests/test_command_center_api.py -s -v
```

---

## Common Test Patterns

### Testing a New Endpoint

1. **Add unit test** in `test_command_center_api.py`:
```python
def test_new_endpoint(self, app_with_mocks):
    """GET /api/new-endpoint returns expected data."""
    from fastapi.testclient import TestClient

    client = TestClient(app_with_mocks)
    response = client.get("/api/new-endpoint")

    assert response.status_code == 200
    data = response.json()

    assert data["success"] is True
    assert "expected_field" in data["data"]
```

2. **Add contract test** in `test_api_contracts.py`:
```python
def test_new_endpoint_contract(self, contract_test_app):
    """New endpoint response matches contract."""
    from fastapi.testclient import TestClient

    client = TestClient(contract_test_app)
    response = client.get("/api/new-endpoint")

    assert response.status_code == 200
    data = response.json()

    # Validate standard response format
    validate_standard_response(data, expect_success=True)

    # Validate required fields
    assert "expected_field" in data["data"]
```

3. **Add integration test** in `integration/test_command_center_integration.py`:
```python
def test_new_endpoint_workflow(self, integration_app):
    """Test complete workflow involving new endpoint."""
    from fastapi.testclient import TestClient

    client = TestClient(integration_app)

    # Step 1: Create resource
    create_response = client.post("/api/resource", json={...})
    assert create_response.status_code == 201

    # Step 2: Use new endpoint
    response = client.get("/api/new-endpoint")
    assert response.status_code == 200

    # Step 3: Verify data consistency
    # ...
```

---

## Test Data Management

### Using Fixtures
Tests use pytest fixtures for mock data:
```python
@pytest.fixture
def mock_agent_registry():
    """Create mock agent registry with sample agents."""
    # Returns mock with predefined agents
```

### Resetting Test State
Tests are isolated - each test gets fresh mocks.
No manual cleanup needed.

---

## Performance Testing

### Measure Test Execution Time
```bash
uv run pytest tests/test_command_center_api.py --durations=10
```

### Parallel Test Execution
```bash
pip install pytest-xdist
uv run pytest tests/test_command_center_api.py -n auto
```

---

## Endpoint Status Reference

### ✅ Fully Working
- `GET /health`
- `GET /api/agents` (with field name differences)
- `GET /api/approvals`
- Authentication system
- Rate limiting

### ⚠️ Partial
- `GET /api/portfolio` (structure mismatch)
- `POST /api/approvals/{id}/approve` (needs async fixes)
- `GET /api/patterns`

### ❌ Not Implemented
- `GET /api/agents/{id}`
- `POST /api/agents/register`
- `GET /api/patterns/{id}`
- `GET /api/tasks`
- `POST /api/tasks`

See `TEST_RESULTS_COMMAND_CENTER_API.md` for full details.

---

## Adding Tests for New Features

### 1. Write Test First (TDD)
```python
def test_new_feature(self):
    """Test for new feature."""
    # Arrange
    client = TestClient(app)

    # Act
    response = client.get("/api/new-feature")

    # Assert
    assert response.status_code == 200
```

### 2. Run Test (Should Fail)
```bash
uv run pytest tests/test_command_center_api.py::test_new_feature -v
```

### 3. Implement Feature
Update `forge_harness/webhook_server.py`

### 4. Run Test Again (Should Pass)
```bash
uv run pytest tests/test_command_center_api.py::test_new_feature -v
```

---

## Resources

- **API Contracts**: `/Users/bogdan/work/FORGE/harness/command_center/docs/API_CONTRACTS.md`
- **Backend Code**: `/Users/bogdan/work/FORGE/harness/forge_harness/webhook_server.py`
- **Test Results**: `/Users/bogdan/work/FORGE/harness/tests/TEST_RESULTS_COMMAND_CENTER_API.md`

---

## FAQ

**Q: Why are some tests skipped?**
A: Tests skip when endpoints aren't implemented yet or dependencies are missing.

**Q: How do I update expected responses?**
A: Modify the test assertions to match the new contract, then update `API_CONTRACTS.md`.

**Q: Tests pass locally but fail in CI?**
A: Check for environment-specific dependencies (Redis, file system paths, etc.).

**Q: How do I test authentication?**
A: See `TestAuthentication` class for examples of testing with/without tokens.

**Q: Can I run tests without Redis?**
A: Some tests may fail. Use mocks or set `FORGE_STATE_STORE_TYPE=memory` for testing.
