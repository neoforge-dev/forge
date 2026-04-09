# FORGE Commit Message Conventions

**Version:** 1.0  
**Last Updated:** 2026-02-08  
**Applies To:** All FORGE domains (NeoForge, LeanVibe, CodeSwiftr, BrandFocus, AdGuild, etc.)

---

## 1. Overview

This document defines the commit message conventions for the FORGE portfolio. Following these conventions enables:

- **Automated changelog generation**
- **Semantic versioning** (based on commit types)
- **Clear release notes** creation
- **Efficient code review** (context at a glance)
- **Historical analysis** and pattern recognition

**Based on analysis of 900+ commits** across NeoForge and LeanVibe domains.

---

## 2. Commit Message Format

All commits MUST follow the [Conventional Commits](https://www.conventionalcommits.org/) specification:

```
<type>(<scope>): <description>

[optional body]

[optional footer(s)]
```

### 2.1 Type (Required)

The type indicates the nature of the change:

| Type | Usage | Frequency* | Example |
|------|-------|------------|---------|
| `feat` | New features | 37% | `feat(auth): add JWT refresh token` |
| `fix` | Bug fixes | 10% | `fix(api): handle null response` |
| `docs` | Documentation | 15% | `docs(readme): update setup instructions` |
| `chore` | Maintenance | 2% | `chore(deps): upgrade dependencies` |
| `test` | Tests only | 2% | `test(auth): add login flow tests` |
| `refactor` | Code restructuring | 1% | `refactor(utils): extract validation logic` |
| `perf` | Performance | <1% | `perf(db): add query caching` |
| `ci` | CI/CD changes | <1% | `ci(github): add matrix builds` |
| `build` | Build system | <1% | `build(docker): optimize image layers` |
| `style` | Formatting | <1% | `style(lint): fix ruff warnings` |

\* Based on NeoForge domain analysis (859 commits)

**Type Selection Guidelines:**
- Use `feat` for any user-facing functionality
- Use `fix` for bug fixes that resolve issues
- Use `docs` for README, CLAUDE.md, PLAN.md updates
- Use `chore` for tooling, dependency updates, config changes
- Use `test` when only test files change
- Use `refactor` when no behavior changes (pure restructuring)

### 2.2 Scope (Optional but Recommended)

Scope identifies the area of the codebase affected:

**Common Scopes (NeoForge/LeanVibe Patterns):**

```
# Domain/Project Level
feat(neoforge): add GraphRAG blueprint
feat(leanvibe): implement card game logic

# Component Level
feat(harness): add fleet monitoring
feat(voice-coach): implement billing
fix(interview-simulator): resolve connection pool

# Feature Level
feat(auth): implement OAuth flow
feat(api): add rate limiting
fix(ui): resolve mobile layout

# Technology Level
feat(backend): add FastAPI endpoints
feat(frontend): implement PWA
refactor(css): consolidate styles
```

**Scope Selection Guidelines:**
- Use project names for cross-domain changes: `(neoforge)`, `(leanvibe)`
- Use component names for subsystem changes: `(harness)`, `(voice-coach)`
- Use feature areas for focused changes: `(auth)`, `(api)`, `(ui)`
- Keep scopes lowercase, hyphenated: `voice-coach`, not `voiceCoach`

### 2.3 Description (Required)

The description explains the change:

**Format Rules:**
- Use **imperative mood** ("add" not "added" or "adds")
- **No period** at the end
- **Lowercase** first letter (unless proper noun)
- **Maximum 72 characters** for subject line
- **Minimum 10 characters** (be descriptive)

✅ **Good Examples:**
```
feat(auth): add JWT refresh token support
fix(api): handle null response in user endpoint
docs(readme): update development setup instructions
```

❌ **Bad Examples:**
```
feat(auth): Added JWT refresh token support.      # Wrong mood, period
fix(api): fix                                       # Too short
Docs: Update README                                 # Wrong case, no scope
feat: implement feature                             # Too vague
```

### 2.4 Body (Optional)

Use the body for additional context when needed:

```
feat(auth): add JWT refresh token support

Implement refresh token rotation to enhance security.
Tokens expire after 7 days and are rotated on each use.

Refs: #123
```

**Body Guidelines:**
- Separate from subject with blank line
- Explain **what** and **why**, not **how**
- Wrap at 72 characters
- Reference issues/PRs when applicable

### 2.5 Footer (Optional)

Use footers for metadata:

```
feat(auth): add JWT refresh token support

BREAKING CHANGE: token format changed from v1 to v2

Refs: #123
Closes: #456
Co-Authored-By: Claude <noreply@anthropic.com>
```

**Common Footer Tags:**
- `Refs: #XXX` - References related issue
- `Closes: #XXX` - Closes issue on merge
- `BREAKING CHANGE:` - Documents breaking changes
- `Co-Authored-By:` - Multiple contributors

---

## 3. Common Patterns from FORGE History

### 3.1 Sprint-Based Commits

FORGE uses sprint and wave-based development:

```
feat: Sprint 9 Wave 6 - perf optimization, deployment runbook
feat: Sprint 9 Wave 7 - Sprint 10 planning, code quality audit
feat: Sprint 9 pre-work - deployment infra, CI/CD, security audit
```

**Pattern:** `type: Sprint X Wave Y - brief description`

### 3.2 Phase-Based Commits

Game/card projects use phased development:

```
feat: Complete Phase 7 database setup and integration validation
feat: Phase 2 - LitElement component architecture
feat: Complete Phase 1 FastAPI backend foundation
```

**Pattern:** `type: [Complete] Phase X - description`

### 3.3 Quality Gate Commits

Commits marking quality milestones:

```
fix(brandfocus-platform): zero mypy errors + 99% coverage (57 tests)
test(voice-coach): VC submodule update - 16 new coverage tests (92%)
feat(voice-coach): VC submodule update - zero ruff lint issues
```

**Pattern:** Include metrics in description: `(X tests, Y% coverage)`

### 3.4 Submodule Update Commits

FORGE uses git submodules extensively:

```
chore: update submodule pointers to latest commits
feat(voice-coach): VC submodule update - P0 fixes, 84% coverage
fix(interview-simulator): IS submodule update - connection pooling fix
```

**Pattern:** `type(project): abbreviation submodule update - changes`

### 3.5 Documentation-First Commits

Documentation is 15% of commits:

```
docs: add comprehensive MVP completion report
docs: update progress with all domain deployments
docs: sync portfolio digest with domain CLAUDE.md files
docs: add session handoff - autonomous fleet orchestration
```

**Pattern:** Documentation reflects project state changes

---

## 4. Best Practices

### 4.1 Commit Frequency

Based on FORGE analysis:

| Activity Level | Commits/Day | Use Case |
|----------------|-------------|----------|
| 🔥 Peak Sprint | 50-60 | Major feature development |
| 🚀 Active Dev | 20-40 | Regular development |
| 📈 Steady | 10-20 | Maintenance, polish |
| 📉 Recovery | <10 | Planning, review |

**Guideline:** Commit early and often. Each commit should represent a logical unit of work.

### 4.2 Commit Size

✅ **Good:**
- One logical change per commit
- Tests included with feature commits
- Documentation updated in same commit as code

❌ **Bad:**
- Mixing multiple features in one commit
- Separating tests from implementation
- "WIP" or "checkpoint" commits

### 4.3 Commit Message Length

| Element | Max Length | Example |
|---------|------------|---------|
| Subject | 72 chars | `feat(auth): add OAuth2 provider support` |
| Body line | 72 chars | (wrap text) |
| Description | 10+ chars | `add user validation` |

### 4.4 Special Commit Types

**Session Handoffs:**
```
docs: session handoff - ios-agent-cli complete
docs: session 3 handoff checkpoint (HB10-14)
checkpoint(#01): fleet dispatch - 5 agents complete
```

**Emergency Fixes:**
```
fix: HOTFIX - resolve critical login bug
fix(security): add defusedxml and fix SQL injection
```

---

## 5. Common Mistakes to Avoid

### 5.1 Inconsistent Casing

```
# ❌ Avoid mixing cases
FEAT: Add feature          # All caps (found in sedma early commits)
Feat: Add feature          # Sentence case
feat: add feature          # ✅ Correct
```

### 5.2 Missing Type Prefix

```
# ❌ Avoid untyped commits
Add terrain countdown UI and meditate action
Update PLAN.md

# ✅ Use type prefixes
feat(game): add terrain countdown UI and meditate action
docs: update PLAN.md to reflect Phase 4 completion
```

### 5.3 Vague Descriptions

```
# ❌ Avoid vague descriptions
feat: implement feature
fix: fix bug
chore: update

# ✅ Be specific
feat(auth): implement OAuth2 Google provider
fix(api): resolve 500 error on null user lookup
chore(deps): upgrade FastAPI to 0.104.0
```

### 5.4 Wrong Type Selection

```
# ❌ Common mistakes
feat: fix login bug        # Should be fix:
fix: update documentation  # Should be docs:
docs: add new API endpoint # Should be feat:
```

---

## 6. Automation Support

### 6.1 Changelog Generation

Conventional commits enable automated changelogs:

```bash
# Generate changelog from commits
npx conventional-changelog -p angular -i CHANGELOG.md -s
```

**Changelog Sections:**
- Features (`feat:`)
- Bug Fixes (`fix:`)
- Documentation (`docs:`)
- Other changes (remaining types)

### 6.2 Semantic Versioning

Commit types determine version bumps:

| Commit Type | Version Impact | Example |
|-------------|----------------|---------|
| `feat:` + `BREAKING CHANGE:` | MAJOR (X.0.0) | `feat(api)!: remove v1 endpoints` |
| `feat:` | MINOR (0.X.0) | `feat(auth): add MFA support` |
| `fix:` | PATCH (0.0.X) | `fix(api): resolve null pointer` |

### 6.3 Release Notes

Format for release notes:

```markdown
## v1.2.0 (2026-02-08)

### Features
- feat(auth): add JWT refresh token support
- feat(api): implement rate limiting

### Bug Fixes
- fix(ui): resolve mobile navigation overlap
- fix(db): correct migration rollback

### Documentation
- docs: update API reference
```

---

## 7. Enforcement

### 7.1 Pre-Commit Hook

Use the FORGE commit validator:

```bash
# Install hook
ln -s .claude/hooks/validate_commit.py .git/hooks/commit-msg

# Or configure in .pre-commit-config.yaml
repos:
  - repo: local
    hooks:
      - id: commit-msg-validator
        name: Validate commit message
        entry: .claude/hooks/validate_commit.py
        language: script
        stages: [commit-msg]
```

### 7.2 CI/CD Validation

Add to GitHub Actions:

```yaml
- name: Validate Commit Messages
  run: |
    pip install gitlint
    gitlint --commits origin/main..HEAD
```

### 7.3 IDE Integration

Configure VS Code/Cursor:

```json
{
  "conventionalCommits.scopes": [
    "harness",
    "neoforge",
    "leanvibe",
    "api",
    "ui",
    "auth",
    "docs"
  ],
  "conventionalCommits.types": [
    { "label": "feat", "description": "New feature" },
    { "label": "fix", "description": "Bug fix" },
    { "label": "docs", "description": "Documentation" },
    { "label": "chore", "description": "Maintenance" },
    { "label": "test", "description": "Tests" },
    { "label": "refactor", "description": "Refactoring" }
  ]
}
```

---

## 8. Domain-Specific Conventions

### 8.1 NeoForge (AI/ML Focus)

```
feat(graph-rag-blueprint): add CI/CD pipeline and Makefile
feat(harness): migrate SDK from claude-code-sdk to claude-agent-sdk
test(harness): all flywheel and notification tests passing (111 tests)
```

### 8.2 LeanVibe (Game Dev Focus)

```
feat: Complete Phase 7 database setup and integration validation
fix: Eliminate all Lit class-field-shadowing warnings in social components
docs: Update PROJECT_STATUS.md - v1.1.0 100% complete
```

### 8.3 CodeSwiftr (Interview Platform)

```
feat(backend): implement Phase 1 FastAPI backend foundation
fix(security): add defusedxml and fix SQL injection vulnerability
```

---

## 9. Examples by Category

### 9.1 Feature Development

```
feat(auth): implement Epic 2 - Authentication & User Management
feat(books): implement Epic 3 - Book Library & Content Delivery
feat(command-center): complete all 44 features across 4 phases
```

### 9.2 Bug Fixes

```
fix(api): resolve 500 error on user lookup with invalid ID
fix(ui): correct mobile navigation menu overlap on iOS
fix(db): resolve migration conflict between v2.1 and v2.2
fix(security): replace JWT tokens with fake test tokens in iOS tests
```

### 9.3 Testing

```
test(auth): add comprehensive login flow tests
test(api): achieve 95% coverage on user endpoints
test(integration): add Playwright E2E tests for critical paths
```

### 9.4 Refactoring

```
refactor(css): delete legacy styles.css (2,663 lines)
refactor(icons): replace emojis with Lucide icons across all domains
refactor(harness): use forge-shared PostHog client
```

### 9.5 Documentation

```
docs: add comprehensive implementation plan
docs(api): document rate limiting and error responses
docs(readme): update local development setup instructions
docs(security): add COPPA compliance guide
```

### 9.6 Maintenance

```
chore(deps): upgrade FastAPI to 0.104.0
chore: update submodule pointers to latest commits
ci(github): add matrix builds for Python 3.11 and 3.12
build(docker): optimize image layers and reduce size by 40%
```

---

## 10. References

- [Conventional Commits Specification](https://www.conventionalcommits.org/)
- [Angular Commit Guidelines](https://github.com/angular/angular/blob/main/CONTRIBUTING.md#commit)
- [Semantic Versioning](https://semver.org/)
- FORGE Git History Analysis: `neoforge-dev/docs/research/GIT_HISTORY_ANALYSIS.md`
- FORGE Git History Analysis: `leanvibe-dev/docs/research/GIT_HISTORY_ANALYSIS.md`

---

## 11. Change Log

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2026-02-08 | Initial release based on 900+ commit analysis |

---

*This document is a living standard. Update as patterns evolve.*
