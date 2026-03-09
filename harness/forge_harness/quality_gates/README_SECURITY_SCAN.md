# Security Scan Gate

Dependency vulnerability scanning quality gate for FORGE Harness.

## Overview

The Security Scan Gate automatically scans project dependencies for known security vulnerabilities using industry-standard tools:

- **Python projects**: `pip-audit` - scans Python packages against OSV database
- **JavaScript/TypeScript projects**: `npm audit` - scans npm packages against npm advisory database

## Features

- **Automatic project detection** - Detects Python (pyproject.toml) or JavaScript (package.json) projects
- **Severity-based blocking** - Blocks on HIGH or CRITICAL vulnerabilities, warns on MEDIUM
- **Smart caching** - Caches scan results for 24 hours to avoid repeated scans
- **Graceful degradation** - Handles missing tools without crashing
- **Detailed reporting** - Shows package, version, CVE/GHSA IDs, and fix versions

## Installation

### Python Projects (pip-audit)

```bash
pip install pip-audit
# or
uv add --dev pip-audit
```

### JavaScript Projects (npm)

npm is typically installed with Node.js. No additional installation needed.

## Usage

### As a Python Module

```python
from forge_harness.quality_gates import run_security_scan

# Scan current directory
result = await run_security_scan()

# Scan specific project
result = await run_security_scan(project_path="/path/to/project")

# Disable cache
result = await run_security_scan(use_cache=False)

# Check result
if not result.passed:
    print(result.summary())
    sys.exit(1)
```

### CLI Usage

```bash
# Scan current directory
python -m forge_harness.quality_gates.security_scan

# Scan specific project
python -m forge_harness.quality_gates.security_scan /path/to/project

# Disable cache and run fresh scan
python -m forge_harness.quality_gates.security_scan --no-cache

# Verbose output
python -m forge_harness.quality_gates.security_scan -v

# Use in pre-commit hook
python -m forge_harness.quality_gates.security_scan || exit 1
```

### With forge-harness CLI

```bash
# Run security scan as part of quality gates
forge-harness quality scan

# Run on specific project
forge-harness quality scan --project-path /path/to/project
```

## Blocking Behavior

The gate uses severity-based blocking:

| Severity | Behavior | Exit Code |
|----------|----------|-----------|
| **CRITICAL** | ❌ Blocks (fails gate) | 1 |
| **HIGH** | ❌ Blocks (fails gate) | 1 |
| **MEDIUM** | ⚠️  Warns (passes gate) | 0 |
| **LOW** | ℹ️  Info (passes gate) | 0 |

Example output:

```
======================================================================
Security Scan Results
======================================================================
Status: FAILED
Project type: python
Scan time: 2.34s

Vulnerabilities found: 2
  CRITICAL: 0
  HIGH: 2
  MEDIUM: 1
  LOW: 0

Details:
  requests@2.25.0 - GHSA-xxxx-xxxx-xxxx [HIGH]: Security issue in requests (fix: 2.31.0)
  urllib3@1.26.0 - CVE-2023-45803 [HIGH]: Cookie request header isn't stripped (fix: 2.0.7)
  certifi@2022.12.7 - GHSA-xqr8-7jwr-rhp7 [MEDIUM]: Removal of e-Tugra root certificate (fix: 2023.7.22)
======================================================================
```

## Caching

Scan results are cached for 24 hours to improve performance:

- **Cache location**: `.forge_cache/security_scans/`
- **Cache key**: Hash of project path, type, and dependency file contents
- **TTL**: 24 hours
- **Invalidation**: Automatic on dependency file changes

### Cache Control

```python
# Use cache (default)
result = await run_security_scan(use_cache=True)

# Force fresh scan
result = await run_security_scan(use_cache=False)

# Clear cache manually
rm -rf .forge_cache/security_scans/
```

## Integration with Pre-commit Hooks

Add to `.git/hooks/pre-commit`:

```bash
#!/bin/bash
set -e

# Run security scan
uv run python -m forge_harness.quality_gates.security_scan

# Run other quality gates
uv run python -m forge_harness.quality_gates.lint_gate --check
uv run python -m forge_harness.quality_gates.pre_commit_tests
```

## Integration with CI/CD

### GitHub Actions

```yaml
- name: Security Scan
  run: |
    pip install pip-audit
    uv run python -m forge_harness.quality_gates.security_scan
```

### Railway

```bash
# railway.json
{
  "build": {
    "builder": "NIXPACKS",
    "buildCommand": "uv sync && uv run python -m forge_harness.quality_gates.security_scan"
  }
}
```

## Data Models

### Vulnerability

```python
@dataclass
class Vulnerability:
    package: str                    # Package name
    version: str                    # Installed version
    vulnerability_id: str           # CVE-xxx or GHSA-xxx
    severity: str                   # CRITICAL, HIGH, MEDIUM, LOW
    description: str                # Vulnerability description
    fixed_version: str | None      # Version with fix (if available)
```

### ScanResult

```python
@dataclass
class ScanResult:
    passed: bool                           # True if no blocking vulnerabilities
    project_type: str                      # "python", "javascript", "unknown"
    vulnerabilities: list[Vulnerability]   # List of found vulnerabilities
    critical_count: int                    # Count of CRITICAL severity
    high_count: int                        # Count of HIGH severity
    medium_count: int                      # Count of MEDIUM severity
    low_count: int                         # Count of LOW severity
    scan_time: float                       # Scan duration in seconds
    cached: bool                           # True if result from cache
    errors: list[str]                      # Errors during scan
```

## Tool-Specific Notes

### pip-audit

- Scans Python packages against the [OSV database](https://osv.dev/)
- Supports virtual environments automatically
- Returns HIGH severity by default (pip-audit doesn't provide severity)
- JSON output format for structured parsing

### npm audit

- Scans npm packages against npm advisory database
- Provides severity levels: critical, high, moderate, low
- Shows dependency chain for vulnerabilities
- JSON output format for structured parsing

## Troubleshooting

### pip-audit not installed

**Error**: `pip-audit not installed. Install with: pip install pip-audit`

**Solution**:
```bash
pip install pip-audit
# or with uv
uv add --dev pip-audit
```

### npm not installed

**Error**: `npm not installed`

**Solution**: Install Node.js and npm from https://nodejs.org/

### Unknown project type

**Error**: `Unknown project type - no pyproject.toml or package.json found`

**Solution**: Ensure you're running the scan from the project root directory that contains either `pyproject.toml` (Python) or `package.json` (JavaScript).

### Cache issues

If you suspect cache issues, force a fresh scan:

```bash
python -m forge_harness.quality_gates.security_scan --no-cache
```

Or clear the cache directory:

```bash
rm -rf .forge_cache/security_scans/
```

## Testing

Run tests with:

```bash
uv run pytest tests/test_security_scan.py -v
```

Test coverage:

```bash
uv run pytest tests/test_security_scan.py --cov=forge_harness.quality_gates.security_scan --cov-report=term-missing
```

## Examples

### Example 1: Clean Scan

```bash
$ python -m forge_harness.quality_gates.security_scan
======================================================================
Security Scan Results
======================================================================
Status: PASSED
Project type: python
Scan time: 1.23s

No vulnerabilities found!
======================================================================
```

### Example 2: Vulnerabilities Found

```bash
$ python -m forge_harness.quality_gates.security_scan
======================================================================
Security Scan Results
======================================================================
Status: FAILED
Project type: javascript
Scan time: 2.45s

Vulnerabilities found: 3
  CRITICAL: 1
  HIGH: 1
  MEDIUM: 1
  LOW: 0

Details:
  lodash@4.17.0 - GHSA-jf85-cpcp-j695 [HIGH]: Prototype Pollution in lodash (fix: available)
  axios@0.21.0 - CVE-2021-3749 [CRITICAL]: Server-Side Request Forgery (fix: available)
  minimist@1.2.5 - GHSA-xvch-5gv4-984h [MEDIUM]: Prototype Pollution (fix: available)
======================================================================
```

### Example 3: Cached Result

```bash
$ python -m forge_harness.quality_gates.security_scan
======================================================================
Security Scan Results
======================================================================
Status: PASSED
Project type: python
Scan time: 0.02s
(Results from cache)

No vulnerabilities found!
======================================================================
```

## API Reference

### SecurityScanGate

Main class for security scanning.

```python
gate = SecurityScanGate(project_path="/path/to/project")
result = await gate.scan(use_cache=True)
```

**Methods**:
- `detect_project_type() -> str` - Detect project type from config files
- `run_pip_audit() -> tuple[list[Vulnerability], list[str]]` - Run pip-audit
- `run_npm_audit() -> tuple[list[Vulnerability], list[str]]` - Run npm audit
- `scan(use_cache: bool = True) -> ScanResult` - Run security scan

### run_security_scan

Factory function for easy usage.

```python
result = await run_security_scan(
    project_path=None,  # defaults to current directory
    use_cache=True,     # use cached results if available
)
```

## Related Gates

- **lint_gate** - Code linting (ruff, eslint)
- **pre_commit_tests** - Affected test runner
- **type_check** - Type checking (mypy, tsc)
- **coverage_gate** - Test coverage enforcement

## Contributing

When modifying the security scan gate:

1. Add tests to `tests/test_security_scan.py`
2. Ensure 70%+ coverage
3. Update this README
4. Test with both Python and JavaScript projects
5. Verify cache behavior

## License

Part of FORGE Harness - MIT License
