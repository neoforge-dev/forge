# Coverage Gate

Pre-commit quality gate that enforces test coverage thresholds to prevent coverage regression.

## Overview

The Coverage Gate runs pytest with coverage analysis on staged Python files and enforces two thresholds:

1. **Overall Coverage**: Must be >= 80%
2. **Per-File Coverage**: Each modified file must be >= 70%

This ensures that:
- Test coverage doesn't degrade over time
- New code is adequately tested
- Modified code maintains high test coverage

## Installation

Coverage gate is part of the `forge_harness.quality_gates` module. Ensure you have the development dependencies installed:

```bash
uv sync --all-extras
```

## Usage

### Python API

```python
from forge_harness.quality_gates import run_coverage_gate

# Run coverage gate on staged files
result = await run_coverage_gate()

if not result.passed:
    print(result.summary())
    sys.exit(1)
```

### CLI

```bash
# Check coverage for staged files (default)
python -m forge_harness.quality_gates.coverage_gate

# With custom thresholds
python -m forge_harness.quality_gates.coverage_gate --overall 85 --file 75

# Verbose mode
python -m forge_harness.quality_gates.coverage_gate -v
```

### Pre-commit Hook

Add to `.git/hooks/pre-commit`:

```bash
#!/bin/bash
# Run coverage gate
python -m forge_harness.quality_gates.coverage_gate || exit 1
```

## How It Works

### 1. Staged File Detection

Gets staged Python files from git:

```bash
git diff --cached --name-only --diff-filter=ACM
```

### 2. Module Coverage Analysis

Converts file paths to Python module names and runs pytest with coverage:

```bash
pytest --cov=module1,module2 --cov-report=json --cov-report=term-missing
```

### 3. Threshold Enforcement

Checks coverage against thresholds:

- **Overall threshold**: Total coverage across all modules
- **File threshold**: Coverage for each individual modified file

### 4. Result Reporting

Generates detailed report with:
- Overall coverage percentage
- Per-file coverage stats
- Files below threshold
- Coverage gaps

## Configuration

### Thresholds

Default thresholds:

```python
OVERALL_THRESHOLD = 80.0  # 80% overall coverage
FILE_THRESHOLD = 70.0     # 70% per-file coverage
```

Custom thresholds:

```python
gate = CoverageGate(
    overall_threshold=85.0,
    file_threshold=75.0,
)
result = await gate.run()
```

### Repository Root

Specify custom repository root:

```python
gate = CoverageGate(repo_root=Path("/path/to/repo"))
```

## Result Model

### CoverageResult

```python
@dataclass
class CoverageResult:
    passed: bool                    # Gate passed/failed
    overall_coverage: float         # Overall coverage %
    threshold: float                # Overall threshold
    file_threshold: float           # Per-file threshold
    files_checked: int              # Number of files checked
    issues: list[CoverageIssue]     # Coverage issues
    stats: list[CoverageStats]      # Per-file stats
    errors: list[str]               # Execution errors
    execution_time: float           # Time in seconds
```

### CoverageStats

```python
@dataclass
class CoverageStats:
    name: str           # File/module name
    statements: int     # Total statements
    covered: int        # Covered statements
    missing: int        # Missing statements
    excluded: int       # Excluded statements
    coverage: float     # Coverage percentage
```

### CoverageIssue

```python
@dataclass
class CoverageIssue:
    file: str                    # File path
    current_coverage: float      # Current coverage %
    required_coverage: float     # Required coverage %
    statements: int              # Total statements
    covered: int                 # Covered statements
    missing: int                 # Missing statements
```

## Example Output

### Passing Gate

```
======================================================================
Coverage Gate Results
======================================================================
Status: PASSED
Overall Coverage: 85.0% (threshold: 80.0%)
Files Checked: 3
Execution Time: 2.45s

Coverage by File:
  ✓ forge_harness/quality_gates/coverage_gate.py: 82.0% (194/237)
  ✓ forge_harness/quality_gates/lint_gate.py: 78.5% (189/241)
  ✓ tests/test_coverage_gate.py: 100.0% (150/150)
======================================================================
```

### Failing Gate

```
======================================================================
Coverage Gate Results
======================================================================
Status: FAILED
Overall Coverage: 65.0% (threshold: 80.0%)
Files Checked: 2
Execution Time: 2.12s

Coverage Issues: 2

Files below threshold:
  <overall>: 65.0% (requires 80.0%, gap: 15.0%)
  forge_harness/new_module.py: 40.0% (requires 70.0%, gap: 30.0%)

Coverage by File:
  ✗ forge_harness/new_module.py: 40.0% (16/40)
  ✓ tests/test_new_module.py: 100.0% (20/20)
======================================================================
```

## Integration with Other Gates

Coverage gate complements other quality gates:

```python
from forge_harness.quality_gates import (
    run_lint_gate,
    run_pre_commit_tests,
    run_coverage_gate,
)

# Run all gates
lint_result = await run_lint_gate(fix=True)
test_result = await run_pre_commit_tests()
coverage_result = await run_coverage_gate()

if not all([lint_result.passed, test_result.passed, coverage_result.passed]):
    sys.exit(1)
```

## CI/CD Integration

### GitHub Actions

```yaml
- name: Run Coverage Gate
  run: |
    python -m forge_harness.quality_gates.coverage_gate
```

### Pre-commit Framework

Add to `.pre-commit-config.yaml`:

```yaml
repos:
  - repo: local
    hooks:
      - id: coverage-gate
        name: Coverage Gate
        entry: python -m forge_harness.quality_gates.coverage_gate
        language: system
        pass_filenames: false
```

## Troubleshooting

### Coverage.json Not Found

If coverage.json is missing, ensure pytest-cov is installed:

```bash
uv pip install pytest-cov
```

### Tests Timing Out

Increase timeout for large test suites:

```python
gate = CoverageGate(repo_root=repo_root)
# Modify timeout in _run_command call
```

### Corrupt .coverage File

Remove the .coverage file:

```bash
rm .coverage
```

## Performance

### Optimization Tips

1. **Incremental Coverage**: Only check modified modules (default behavior)
2. **Parallel Test Execution**: Use pytest-xdist for faster tests
3. **Coverage Caching**: Reuse coverage data when possible

### Typical Performance

- **Small project** (5-10 modules): 1-3 seconds
- **Medium project** (20-50 modules): 3-10 seconds
- **Large project** (100+ modules): 10-30 seconds

## Best Practices

1. **Set Realistic Thresholds**: Start with achievable thresholds and gradually increase
2. **Exclude Generated Code**: Use `.coveragerc` to exclude auto-generated files
3. **Run Locally**: Run coverage gate before pushing to catch issues early
4. **Monitor Trends**: Track coverage trends over time
5. **Test-First Development**: Write tests before implementing features

## Testing

Run coverage gate tests:

```bash
uv run pytest tests/test_coverage_gate.py -v
```

With coverage:

```bash
uv run pytest tests/test_coverage_gate.py --cov=forge_harness.quality_gates.coverage_gate
```

## Related Gates

- **Lint Gate**: Code style and formatting (`lint_gate.py`)
- **Pre-commit Tests**: Run affected tests (`pre_commit_tests.py`)
- **Type Check Gate**: Static type checking (`type_check.py`)

## References

- [pytest-cov documentation](https://pytest-cov.readthedocs.io/)
- [Coverage.py](https://coverage.readthedocs.io/)
- [FORGE Harness Quality Gates](README.md)
