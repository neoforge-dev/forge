---
name: auto-test-runner
description: Automatically run relevant tests after code changes (model-invoked)
auto_execute: true
disable-model-invocation: false
allowed-tools: [Bash, Read, Grep]
---

# Auto Test Runner

Automatically detects and runs relevant tests after code changes. Helps ensure code quality without manual intervention.

## When to Use

**Automatic triggers (model-invoked):**
- After editing Python source files in `app/` or `src/`
- After editing test files in `tests/`
- After modifying configuration that affects tests

**Manual invocation:**
- `/auto-test-runner` - Run tests for current changes
- `/auto-test-runner --all` - Run full test suite

## Behavior

### 1. Detect Changed Files

```bash
# Get recently modified files
git diff --name-only HEAD~1
git status --porcelain
```

### 2. Map Files to Tests

| Source Pattern | Test Pattern |
|---------------|--------------|
| `app/api/routes/*.py` | `tests/e2e/test_*.py` |
| `app/services/*.py` | `tests/unit/test_*.py` |
| `app/models/*.py` | `tests/integration/test_*.py` |
| `tests/*.py` | Run the test file directly |

### 3. Run Tests

**Python (pytest):**
```bash
cd backend && uv run pytest tests/path/to/test.py -v
```

**TypeScript (vitest):**
```bash
cd frontend && npm run test -- --filter=TestFile
```

### 4. Report Results

**On Success:**
```
✅ Tests passed: 5/5
   - test_session_creation.py: 4 passed
   - test_billing.py: 1 passed
```

**On Failure:**
```
❌ Tests failed: 2/5
   - test_share_links.py:45: AssertionError: Expected 200, got 403
   - test_billing.py:78: KeyError: 'user_id'

Suggested fixes:
1. Check authentication in share endpoint
2. Verify billing payload includes user_id
```

## Configuration

### Test Commands by Project

| Project | Command |
|---------|---------|
| voice-coach | `cd app/backend && uv run pytest` |
| interview-simulator | `cd backend && uv run pytest` |
| tech-debt-analyzer | `cd backend && uv run pytest` |

### Skip Patterns

Don't run tests for:
- Documentation changes (`*.md`)
- Config changes (`*.json`, `*.yaml`) unless test config
- Static assets (`*.css`, `*.png`)

## Example Output

```
🧪 Auto Test Runner

Changed files detected:
  - app/api/routes/billing.py (modified)
  - tests/e2e/test_billing_e2e.py (modified)

Running relevant tests...

$ uv run pytest tests/e2e/test_billing_e2e.py -v

======================== test session starts =========================
tests/e2e/test_billing_e2e.py::TestWebhookSecurity::test_webhook_requires_signature PASSED
tests/e2e/test_billing_e2e.py::TestWebhookSecurity::test_webhook_invalid_signature PASSED
tests/e2e/test_billing_e2e.py::TestWebhookSecurity::test_webhook_rate_limit PASSED
========================= 3 passed in 0.45s ==========================

✅ All tests passed!
```

## Error Handling

- **Test timeout**: Default 5 minutes, configurable
- **Missing dependencies**: Suggest `uv sync` or `npm install`
- **Database not running**: Suggest `docker-compose up -d`
