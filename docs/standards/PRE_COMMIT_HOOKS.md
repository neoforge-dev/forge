# FORGE Pre-Commit Hooks Configuration

**Version:** 1.0  
**Last Updated:** 2026-02-08  
**Purpose:** Enforce commit conventions and code quality

---

## 1. Overview

Pre-commit hooks validate changes before they're committed, ensuring:

- ✅ Commit messages follow FORGE conventions
- ✅ Code passes linting and type checks
- ✅ Tests pass before commits
- ✅ Security issues are caught early
- ✅ Secrets aren't accidentally committed

---

## 2. Installation

### 2.1 Install Pre-Commit

```bash
# Using pip
pip install pre-commit

# Using uv (FORGE standard)
uv tool install pre-commit

# Or via Homebrew
brew install pre-commit
```

### 2.2 Install Hooks in Repository

```bash
# From repository root
cd /Users/moltbot/work/FORGE

# Install hooks
pre-commit install

# Also install commit-msg hook for commit validation
pre-commit install --hook-type commit-msg
```

---

## 3. Commit Message Validation

### 3.1 Using FORGE Custom Validator

`.pre-commit-config.yaml`:

```yaml
repos:
  - repo: local
    hooks:
      - id: commit-message-validator
        name: Validate commit message
        entry: .claude/hooks/validate_commit.py
        language: script
        stages: [commit-msg]
        always_run: true
```

### 3.2 Using Commitlint (Alternative)

`.pre-commit-config.yaml`:

```yaml
repos:
  - repo: https://github.com/alessandrojcm/commitlint-pre-commit-hook
    rev: v9.11.0
    hooks:
      - id: commitlint
        stages: [commit-msg]
        additional_dependencies: ['@commitlint/config-conventional']
```

`commitlint.config.js`:

```javascript
module.exports = {
  extends: ['@commitlint/config-conventional'],
  rules: {
    'type-enum': [
      2,
      'always',
      [
        'feat',     // New feature
        'fix',      // Bug fix
        'docs',     // Documentation
        'style',    // Formatting
        'refactor', // Code restructuring
        'test',     // Tests
        'chore',    // Maintenance
        'perf',     // Performance
        'ci',       // CI/CD
        'build',    // Build system
      ],
    ],
    'scope-case': [2, 'always', 'kebab-case'],
    'subject-case': [2, 'always', 'lower-case'],
    'subject-full-stop': [2, 'never', '.'],
    'header-max-length': [2, 'always', 72],
    'subject-min-length': [2, 'always', 10],
  },
};
```

### 3.3 Validation Rules Summary

| Rule | Configuration | Error Message |
|------|---------------|---------------|
| Type | Must be from allowed list | "Invalid commit type" |
| Scope | Lowercase, kebab-case | "Scope must be kebab-case" |
| Subject | Lowercase, no period | "Subject must be lowercase" |
| Length | Max 72 chars | "Header too long" |
| Min Length | Min 10 chars | "Subject too short" |

---

## 4. Complete FORGE Pre-Commit Configuration

### 4.1 Standard Configuration

`.pre-commit-config.yaml`:

```yaml
# Pre-commit hooks for FORGE projects
# Install with: pre-commit install && pre-commit install --hook-type commit-msg

repos:
  # ==================== COMMIT MESSAGE ====================
  - repo: local
    hooks:
      - id: commit-message-validator
        name: Validate commit message (FORGE conventions)
        entry: .claude/hooks/validate_commit.py
        language: script
        stages: [commit-msg]
        always_run: true

  # ==================== GENERAL ====================
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v4.4.0
    hooks:
      - id: check-added-large-files
        args: ['--maxkb=1000']
      - id: check-merge-conflict
      - id: check-yaml
      - id: check-json
      - id: check-toml
      - id: check-xml
      - id: check-shebang-scripts
      - id: check-executables-have-shebangs
      - id: check-case-conflict
      - id: check-docstring-first
      - id: check-ast
      - id: debug-statements
      - id: trailing-whitespace
      - id: end-of-file-fixer

  # ==================== PYTHON ====================
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.1.9
    hooks:
      - id: ruff
        args: [--fix, --exit-non-zero-on-fix]
      - id: ruff-format

  - repo: https://github.com/pre-commit/mirrors-mypy
    rev: v1.7.1
    hooks:
      - id: mypy
        additional_dependencies: [types-all]
        args: [--ignore-missing-imports]

  # ==================== SECURITY ====================
  - repo: https://github.com/pycqa/bandit
    rev: 1.7.5
    hooks:
      - id: bandit
        args: ['-c', '.bandit']
        pass_filenames: false
        always_run: true

  - repo: https://github.com/Yelp/detect-secrets
    rev: v1.4.0
    hooks:
      - id: detect-secrets
        args: ['--baseline', '.secrets.baseline']

  # ==================== JAVASCRIPT/TYPESCRIPT ====================
  - repo: https://github.com/pre-commit/mirrors-eslint
    rev: v8.44.0
    hooks:
      - id: eslint
        types: [file]
        files: \.(js|ts|tsx|jsx)$
        additional_dependencies:
          - eslint@8.44.0
          - "@typescript-eslint/eslint-plugin"
          - "@typescript-eslint/parser"
          - eslint-plugin-security

  # ==================== DOCKER ====================
  - repo: https://github.com/hadolint/hadolint
    rev: v2.12.0
    hooks:
      - id: hadolint-docker
        args: [--ignore, DL3008, --ignore, DL3009]

  # ==================== FORGE CUSTOM ====================
  - repo: local
    hooks:
      - id: check-forge-conventions
        name: Check FORGE conventions
        entry: python -c "
import sys
import re

# Check for FORGE-specific patterns
files = sys.argv[1:]
errors = []

for f in files:
    if 'CLAUDE.md' in f and not f.endswith('CLAUDE.md'):
        errors.append(f'CLAUDE.md should be at project root: {f}')
    if f.endswith('.md') and 'docs/' not in f and 'CLAUDE.md' not in f:
        # Check if markdown files are in appropriate locations
        pass

if errors:
    for e in errors:
        print(f'ERROR: {e}')
    sys.exit(1)
"
        language: system
        pass_filenames: true
        files: \.(md|py|js|ts)$

# ==================== CONFIGURATION ====================
default_stages: [commit]
fail_fast: false

# CI configuration
ci:
  autofix_commit_msg: |
    [pre-commit.ci] auto fixes from pre-commit.com hooks

    for more information, see https://pre-commit.ci
  autofix_prs: true
  autoupdate_branch: ''
  autoupdate_commit_msg: '[pre-commit.ci] pre-commit autoupdate'
  autoupdate_schedule: weekly
  skip: []
  submodules: false
```

### 4.2 Project-Specific Overrides

For Python-heavy projects:

```yaml
# Additional Python hooks
- repo: https://github.com/pycqa/flake8
  rev: 6.0.0
  hooks:
    - id: flake8
      additional_dependencies:
        - flake8-bandit
        - flake8-bugbear
        - flake8-comprehensions
```

For JavaScript-heavy projects:

```yaml
# Additional JS hooks
- repo: https://github.com/pre-commit/mirrors-prettier
  rev: v3.0.0
  hooks:
    - id: prettier
      types_or: [javascript, typescript, json, yaml, markdown]
```

---

## 5. GitHub Actions Integration

### 5.1 Pre-Commit CI Workflow

`.github/workflows/pre-commit.yml`:

```yaml
name: Pre-Commit Checks

on:
  pull_request:
  push:
    branches: [main, develop]

jobs:
  pre-commit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Install uv
        run: |
          curl -LsSf https://astral.sh/uv/install.sh | sh
          echo "$HOME/.cargo/bin" >> $GITHUB_PATH

      - name: Install pre-commit
        run: uv tool install pre-commit

      - name: Cache pre-commit hooks
        uses: actions/cache@v3
        with:
          path: ~/.cache/pre-commit
          key: pre-commit-${{ hashFiles('.pre-commit-config.yaml') }}

      - name: Run pre-commit hooks
        run: pre-commit run --all-files --show-diff-on-failure

      - name: Validate commit messages
        if: github.event_name == 'pull_request'
        run: |
          pip install gitlint
          gitlint --commits origin/${{ github.base_ref }}..HEAD
```

### 5.2 Commit Message Linting Only

```yaml
name: Commit Message Lint

on:
  pull_request:
    types: [opened, synchronize]

jobs:
  commitlint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Setup Node
        uses: actions/setup-node@v4
        with:
          node-version: '20'

      - name: Install commitlint
        run: |
          npm install -g @commitlint/cli @commitlint/config-conventional

      - name: Validate commits
        run: |
          commitlint --from ${{ github.event.pull_request.base.sha }} --to ${{ github.event.pull_request.head.sha }} --verbose
```

---

## 6. IDE Integration

### 6.1 VS Code / Cursor

**Extensions:**
- `vivaxy.vscode-conventional-commits` - Guided commit messages
- `esbenp.prettier-vscode` - Code formatting
- `ms-python.python` - Python linting

**Settings** (`.vscode/settings.json`):

```json
{
  "conventionalCommits.scopes": [
    "harness",
    "neoforge",
    "leanvibe",
    "codeswiftr",
    "brandfocus",
    "adguild",
    "api",
    "ui",
    "auth",
    "db",
    "docs",
    "test"
  ],
  "conventionalCommits.types": [
    {
      "label": "feat",
      "description": "✨ New feature"
    },
    {
      "label": "fix",
      "description": "🐛 Bug fix"
    },
    {
      "label": "docs",
      "description": "📝 Documentation"
    },
    {
      "label": "chore",
      "description": "🔧 Maintenance"
    },
    {
      "label": "test",
      "description": "✅ Tests"
    },
    {
      "label": "refactor",
      "description": "♻️ Refactoring"
    }
  ],
  "editor.formatOnSave": true,
  "python.formatting.provider": "ruff",
  "python.linting.enabled": true,
  "python.linting.ruffEnabled": true
}
```

### 6.2 JetBrains IDEs

**Commit Message Template:**

```
# Settings > Editor > File and Code Templates > Includes > Commit Message
type(scope): description

# Types: feat, fix, docs, chore, test, refactor, perf, ci, build, style
# Scope: project name, component, or feature area
# Description: imperative mood, lowercase, no period
```

### 6.3 Vim/Neovim

**Vim Plugin** (vim-conventional-commits):

```vim
" .vimrc or init.vim
Plug 'vim-conventional-commits'

" Configure scopes
let g:conventional_commits_scopes = [
  \ 'harness',
  \ 'neoforge',
  \ 'leanvibe',
  \ 'api',
  \ 'ui',
  \ 'auth',
  \ 'docs'
\ ]
```

---

## 7. Custom Hooks

### 7.1 FORGE Commit Validator

Location: `.claude/hooks/validate_commit.py` (already exists)

```python
#!/usr/bin/env python3
"""Commit message validation hook for FORGE.

Validates commit messages follow conventional commit format.
"""

import re
import sys

VALID_TYPES = [
    "feat", "fix", "docs", "style", "refactor",
    "test", "chore", "perf", "ci", "build"
]

COMMIT_PATTERN = re.compile(
    r"^(?P<type>" + "|".join(VALID_TYPES) + r")"
    r"(?:\((?P<scope>[a-z0-9-]+)\))?"
    r": (?P<description>.+)$",
    re.IGNORECASE
)

ALLOWED_PATTERNS = [
    r"^Merge branch",
    r"^Merge pull request",
    r"^Revert ",
    r"^Initial commit",
]


def validate_commit_message(message: str) -> tuple[bool, str]:
    """Validate a commit message."""
    subject = message.split("\n")[0].strip()

    if not subject:
        return False, "Commit message is empty"

    for pattern in ALLOWED_PATTERNS:
        if re.match(pattern, subject, re.IGNORECASE):
            return True, "Special commit pattern allowed"

    match = COMMIT_PATTERN.match(subject)
    if not match:
        return False, (
            f"Invalid commit format. Expected: type(scope): description\n"
            f"Valid types: {', '.join(VALID_TYPES)}\n"
            f"Example: feat(auth): add JWT refresh token support"
        )

    description = match.group("description")

    if len(description) < 10:
        return False, "Description too short (min 10 characters)"

    if len(subject) > 72:
        return False, f"Subject line too long ({len(subject)} > 72 characters)"

    if description.endswith("."):
        return False, "Description should not end with a period"

    if description[0].isupper():
        return False, "Description should start with lowercase"

    return True, "Valid commit message"


def main():
    if len(sys.argv) < 2:
        message = sys.stdin.read().strip()
    else:
        message = sys.argv[1]

    if not message:
        print("No commit message provided", file=sys.stderr)
        sys.exit(1)

    passed, result = validate_commit_message(message)

    if passed:
        print(f"✅ {result}")
        sys.exit(0)
    else:
        print(f"❌ {result}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
```

### 7.2 Scope Enforcer

`.claude/hooks/enforce_scope.py`:

```python
#!/usr/bin/env python3
"""Enforce scope conventions for multi-component projects."""

import re
import sys
from pathlib import Path

# Define allowed scopes per project
PROJECT_SCOPES = {
    "harness": [
        "harness", "cli", "api", "fleet", "orchestrator",
        "quality", "test", "docs"
    ],
    "neoforge": [
        "neoforge", "graph-rag", "synapse", "mindflow",
        "discover-ai", "orchestrator-cli"
    ],
    "leanvibe": [
        "leanvibe", "sedma", "clan-wars", "rummy-rivals",
        "technical-debt-analyzer"
    ],
}


def get_project_scopes():
    """Detect project and return allowed scopes."""
    cwd = Path.cwd()
    
    for project, scopes in PROJECT_SCOPES.items():
        if project in str(cwd):
            return scopes
    
    return []


def validate_scope(message: str) -> tuple[bool, str]:
    """Validate scope is appropriate for project."""
    subject = message.split("\n")[0].strip()
    
    # Extract scope
    scope_match = re.search(r"\(([a-z0-9-]+)\):", subject)
    if not scope_match:
        return True, "No scope (optional)"
    
    scope = scope_match.group(1)
    allowed_scopes = get_project_scopes()
    
    if not allowed_scopes:
        return True, "No scope restrictions"
    
    if scope in allowed_scopes:
        return True, f"Valid scope: {scope}"
    
    return False, (
        f"Invalid scope: {scope}\n"
        f"Allowed scopes: {', '.join(allowed_scopes)}"
    )


def main():
    message = sys.stdin.read().strip() if len(sys.argv) < 2 else sys.argv[1]
    
    passed, result = validate_scope(message)
    
    if passed:
        print(f"✅ {result}")
        sys.exit(0)
    else:
        print(f"⚠️ {result}", file=sys.stderr)
        # Don't fail, just warn
        sys.exit(0)


if __name__ == "__main__":
    main()
```

---

## 8. Troubleshooting

### 8.1 Hook Not Running

```bash
# Check if hooks are installed
ls -la .git/hooks/

# Reinstall hooks
pre-commit uninstall
pre-commit install
pre-commit install --hook-type commit-msg

# Check hook file
head -5 .git/hooks/commit-msg
```

### 8.2 Bypassing Hooks (Emergency)

```bash
# Skip all hooks (not recommended)
git commit -m "emergency fix" --no-verify

# Skip specific hook
SKIP=commit-message-validator git commit -m "test"
```

### 8.3 Running Hooks Manually

```bash
# Run all hooks on all files
pre-commit run --all-files

# Run specific hook
pre-commit run ruff --all-files

# Run commit-msg hook
echo "feat: test" | .claude/hooks/validate_commit.py
```

### 8.4 Updating Hooks

```bash
# Update all hooks to latest versions
pre-commit autoupdate

# Update specific hook
pre-commit autoupdate --repo https://github.com/astral-sh/ruff-pre-commit
```

---

## 9. Best Practices

### 9.1 Hook Performance

| Hook | Avg Time | Optimization |
|------|----------|--------------|
| commit-msg | <100ms | Keep validator simple |
| ruff | 1-3s | Use cache |
| mypy | 5-10s | Run only on changed files |
| bandit | 2-5s | Skip on docs-only changes |
| tests | 30s+ | Run in CI, not pre-commit |

**Recommendation:** Keep pre-commit hooks under 10 seconds total.

### 9.2 Staged vs All Files

```yaml
# Only run on staged files (default)
- id: ruff
  stages: [commit]

# Run on all files in CI
- id: ruff
  stages: [commit, push, manual]
```

### 9.3 Conditional Hooks

```yaml
# Only run for specific file types
- id: eslint
  files: \.(js|ts|tsx)$

# Exclude certain files
- id: ruff
  exclude: ^(migrations/|generated/)
```

---

## 10. References

- Pre-commit documentation: https://pre-commit.com/
- Commitlint: https://commitlint.js.org/
- FORGE Commit Conventions: `docs/standards/COMMIT_CONVENTIONS.md`
- FORGE Commit Patterns: `.forge/memories/git-commit-patterns.md`

---

*Configuration validated with FORGE portfolio standards*
