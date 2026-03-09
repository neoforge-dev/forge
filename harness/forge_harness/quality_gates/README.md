# Quality Gates

Quality gates for FORGE harness - automated checks that run at various lifecycle points to ensure code quality.

## Available Gates

### Lint Gate

Pre-commit linting gate that checks staged files for code quality issues.

**Supported Languages:**
- Python (via `ruff`)
- JavaScript/TypeScript (via `eslint`)

**Features:**
- Automatic file detection from git staging area
- Auto-fix capability
- Detailed error reporting with line numbers
- Blocks commits if unfixable errors remain

**Usage:**

```bash
# Check only
python -m forge_harness.quality_gates.lint_gate --check

# Auto-fix
python -m forge_harness.quality_gates.lint_gate --fix
```

**Documentation:** See [LINT_GATE.md](../../docs/LINT_GATE.md)

### Pre-commit Tests Gate

Runs only tests affected by staged files to provide fast pre-commit validation.

**Supported Languages:**
- Python (via `pytest`)
- JavaScript/TypeScript (via `jest`/`vitest`)

**Features:**
- Smart test discovery based on import analysis
- Runs only affected tests (faster than full suite)
- Detailed failure reporting
- Blocks commits if tests fail

**Usage:**

```bash
# Run affected tests
python -m forge_harness.quality_gates.pre_commit_tests

# Dry-run (see what would be tested)
python -m forge_harness.quality_gates.pre_commit_tests --check
```

**Documentation:** See [PRE_COMMIT_TESTS.md](../../docs/PRE_COMMIT_TESTS.md)

### Coverage Gate

Enforces test coverage thresholds to prevent coverage regression.

**Thresholds:**
- Overall coverage: >= 80%
- Per-file coverage: >= 70% for modified files

**Features:**
- Runs pytest with coverage on staged modules
- Per-file and overall coverage validation
- Detailed coverage reporting
- Blocks commits if coverage falls below threshold

**Usage:**

```bash
# Check coverage
python -m forge_harness.quality_gates.coverage_gate

# Custom thresholds
python -m forge_harness.quality_gates.coverage_gate --overall 85 --file 75
```

**Documentation:** See [COVERAGE_GATE.md](COVERAGE_GATE.md)

## Implementing New Gates

To add a new quality gate:

1. Create a new module in `forge_harness/quality_gates/`
2. Follow the pattern established by `lint_gate.py`:
   - Create a result dataclass
   - Implement gate logic in a class
   - Provide factory function for easy usage
   - Add CLI entry point
3. Add exports to `__init__.py`
4. Write comprehensive tests
5. Document usage and integration

### Example Structure

```python
"""
New Gate - Description
=====================

Purpose and functionality.
"""

from dataclasses import dataclass
from pathlib import Path

@dataclass
class NewGateResult:
    passed: bool
    # ... other fields

class NewGate:
    def __init__(self, ...):
        pass

    async def run(self) -> NewGateResult:
        # Gate logic
        pass

async def run_new_gate(...) -> NewGateResult:
    """Factory function."""
    gate = NewGate(...)
    return await gate.run()

def main() -> int:
    """CLI entry point."""
    pass

if __name__ == "__main__":
    sys.exit(main())
```

## Testing

Run all quality gate tests:

```bash
pytest tests/test_*_gate.py -v
```

Test specific gate:

```bash
pytest tests/test_lint_gate.py -v
```

## Integration with Harness

Quality gates can be integrated into:

1. **Pre-commit hooks**: Block commits with issues
2. **CI/CD pipelines**: Automated quality checks
3. **Ralph loop**: Verify code quality during autonomous development
4. **Flywheel**: Quality gates for each iteration
5. **Approval queue**: Quality gate failures trigger human review

## Architecture

```
quality_gates/
├── __init__.py              # Exports
├── lint_gate.py             # Linting gate
├── pre_commit_tests.py      # Pre-commit test runner
├── coverage_gate.py         # Coverage threshold enforcement
├── COVERAGE_GATE.md         # Coverage gate documentation
└── README.md                # This file
```

## Configuration

Quality gates respect project-specific configurations:

- **Python**: `pyproject.toml`, `ruff.toml`
- **JavaScript/TypeScript**: `.eslintrc.*`, `package.json`

## Best Practices

1. **Fail fast**: Gates should fail quickly on critical issues
2. **Clear feedback**: Provide specific error messages with line numbers
3. **Auto-fix when possible**: Offer `--fix` mode for correctable issues
4. **Logging**: Use structured logging from `forge_harness.logging_config`
5. **Async**: Implement gates asynchronously for better performance
6. **Testing**: Maintain 80%+ test coverage

## Future Gates

Planned quality gates:

- **Security Gate**: Check for security vulnerabilities
- **Dependency Gate**: Check for outdated or vulnerable dependencies
- **Documentation Gate**: Ensure public APIs are documented

## Related Documentation

- [Lint Gate Documentation](../../docs/LINT_GATE.md)
- [Quality Loop](../../docs/QUALITY_LOOP.md)
- [Testing Guide](../../docs/TESTING.md)
