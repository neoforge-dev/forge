---
name: auto-security-scan
description: Scan code changes for security vulnerabilities (model-invoked)
auto_execute: true
disable-model-invocation: false
allowed-tools: [Bash, Read, Grep]
---

# Auto Security Scan

Automatically scans code changes for common security vulnerabilities. Runs after edits to sensitive files.

## When to Use

**Automatic triggers (model-invoked):**
- After editing authentication code (`auth/`, `login`, `jwt`)
- After editing API routes handling user data
- After modifying database queries
- After adding new dependencies

**Manual invocation:**
- `/auto-security-scan` - Scan recent changes
- `/auto-security-scan --full` - Full codebase scan

## Checks Performed

### 1. Secret Detection

```bash
# Check for hardcoded secrets
grep -rn "api_key\s*=\s*['\"]" --include="*.py"
grep -rn "password\s*=\s*['\"][^'\"]+['\"]" --include="*.py"
grep -rn "sk-[a-zA-Z0-9]" --include="*.py"  # OpenAI keys
grep -rn "ghp_" --include="*.py"             # GitHub tokens
```

### 2. SQL Injection

Look for string formatting in SQL:
```python
# BAD: SQL injection vulnerable
cursor.execute(f"SELECT * FROM users WHERE id = {user_id}")

# GOOD: Parameterized query
cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
```

### 3. XSS Prevention

Check for unsanitized output:
```python
# BAD: XSS vulnerable
return f"<div>{user_input}</div>"

# GOOD: Escaped output
from markupsafe import escape
return f"<div>{escape(user_input)}</div>"
```

### 4. Authentication Gaps

- Missing `@require_auth` decorators
- Endpoints without product access checks
- Hardcoded user IDs in routes

### 5. Dependency Vulnerabilities

```bash
# Python
pip-audit --strict

# Node
npm audit
```

## Severity Levels

| Level | Description | Action |
|-------|-------------|--------|
| CRITICAL | Immediate exploit risk | Block commit |
| HIGH | Significant vulnerability | Require fix |
| MEDIUM | Potential issue | Warn, suggest fix |
| LOW | Best practice | Info only |

## Example Output

```
🔒 Security Scan Results

Scanned files:
  - app/api/routes/billing.py
  - app/auth/dependencies.py

Findings:

⚠️ MEDIUM: Potential logging of sensitive data
   app/api/routes/billing.py:45
   > logger.info(f"Processing payment for user {user_id}: {payload}")

   Suggestion: Redact payload in logs
   > logger.info(f"Processing payment for user {user_id}")

✅ PASS: No SQL injection patterns found
✅ PASS: All routes have auth decorators
✅ PASS: No hardcoded secrets detected

Summary: 0 critical, 0 high, 1 medium, 0 low
```

## Integration with Human Gates

When CRITICAL or HIGH issues are found:
1. Block the action
2. Escalate to human review
3. Provide fix recommendations

```
🚨 CRITICAL: Security issue detected

Issue: Hardcoded API key found
File: app/services/payment.py:23
Code: STRIPE_KEY = "sk_live_xxx..."

This requires human review before proceeding.
Would you like to:
1. Fix now (recommended)
2. Escalate to human
3. Skip (not recommended)
```

## Configuration

### Scan Patterns

```python
SENSITIVE_FILES = [
    "auth/",
    "login",
    "password",
    "jwt",
    "token",
    "secret",
    "credential",
    "payment",
    "billing",
]

SKIP_PATTERNS = [
    "test_",
    "_test.py",
    "mock_",
    "fixture",
]
```

### False Positive Handling

Mark known false positives:
```python
# security: ignore - test data only
TEST_API_KEY = "test-key-not-real"
```
