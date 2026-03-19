---
name: mvp-spec-writer
description: Auto-generate features.json from exploration reports with prioritized backlog and complexity estimates
auto_execute: true
disable-model-invocation: false
allowed-tools: [Read, Write, Bash]
---

# MVP Spec Writer Skill

Converts exploration reports into actionable features.json files with prioritized backlog, acceptance criteria, and complexity estimates. Bridges the gap between exploration and building.

## When to Use

- After completing `/niche-explorer`
- Starting a new MVP build
- Refining an existing features.json
- Creating structured backlog from product requirements

## Prerequisites

- Exploration report exists (from `/niche-explorer`)
- Domain and project directories exist
- Understanding of FORGE feature schema

## Inputs

```yaml
exploration: babybit-es/explorations/pedi-sync-exploration.md
project_name: pedi-sync
constraints:
  backend: FastAPI
  auth: JWT
  max_p0_features: 7
  target_completion: 2 weeks
```

## Workflow

### Step 1: Parse Exploration Report

Extract key information:
- MVP name and description
- Problem statement
- Target users
- Differentiator
- Tech stack requirements
- Success metrics

### Step 2: Generate Feature Backlog

Create features following this priority framework:

**P0 (Must Have - MVP Blocker)**
- Core value proposition features
- Essential user flows
- Basic auth/security
- Minimum data model

**P1 (Should Have - Launch Enhancer)**
- Enhanced user experience
- Additional workflows
- Performance optimizations
- Analytics/tracking

**P2 (Nice to Have - Post-Launch)**
- Advanced features
- Integrations
- Admin tools
- Optimization

### Step 3: Structure Each Feature

```json
{
  "id": "PS-001",
  "title": "Project scaffold with FastAPI backend",
  "priority": "P0",
  "type": "backend|frontend|infrastructure|content",
  "complexity": "low|medium|high",
  "estimated_hours": 4,
  "acceptance_criteria": [
    "FastAPI app with health endpoint returns 200",
    "SQLAlchemy async setup with User model",
    "Pytest configuration with 1+ passing test",
    "Docker setup with hot reload"
  ],
  "dependencies": [],
  "technical_notes": "Use fastapi-service-template skill"
}
```

### Step 4: Estimate Complexity

Use these guidelines:

**Low Complexity** (2-4 hours)
- CRUD endpoints
- Simple UI components
- Standard auth flows
- Basic data models

**Medium Complexity** (4-8 hours)
- Business logic workflows
- Complex forms
- File uploads
- Email notifications

**High Complexity** (8-16 hours)
- Third-party integrations
- Real-time features
- Complex algorithms
- Multi-step workflows

### Step 5: Build Dependency Graph

Identify feature dependencies:
- Infrastructure before backend
- Backend models before API endpoints
- API endpoints before frontend
- Auth before protected routes

### Step 6: Generate features.json

Output complete features.json:

```json
{
  "project": "pedi-sync",
  "domain": "babybit-es",
  "version": "1.0.0",
  "description": "Share baby nutrition data with pediatricians",
  "tech_stack": {
    "backend": "FastAPI",
    "frontend": "React PWA",
    "database": "PostgreSQL",
    "auth": "JWT",
    "deployment": "Railway + Cloudflare Pages"
  },
  "compliance": ["COPPA"],
  "success_metrics": [
    "100 active parents in 30 days",
    "70% user retention after 7 days",
    "4.5+ app store rating"
  ],
  "features": [
    {
      "id": "PS-001",
      "title": "Project scaffold with FastAPI backend",
      "priority": "P0",
      "type": "infrastructure",
      "complexity": "medium",
      "estimated_hours": 4,
      "acceptance_criteria": [
        "FastAPI app with health endpoint",
        "SQLAlchemy async setup",
        "Pytest configuration",
        "Docker setup"
      ],
      "dependencies": [],
      "technical_notes": "Use /fastapi-service-template skill"
    },
    {
      "id": "PS-002",
      "title": "User authentication with JWT",
      "priority": "P0",
      "type": "backend",
      "complexity": "medium",
      "estimated_hours": 6,
      "acceptance_criteria": [
        "Parent account registration",
        "Login with email/password",
        "JWT token generation",
        "Protected endpoints",
        "Password reset flow"
      ],
      "dependencies": ["PS-001"],
      "technical_notes": "Parents are account owners (COPPA compliance)"
    },
    {
      "id": "PS-003",
      "title": "Feeding log data model",
      "priority": "P0",
      "type": "backend",
      "complexity": "medium",
      "estimated_hours": 5,
      "acceptance_criteria": [
        "FeedingLog model with timestamp, food, quantity",
        "Baby profile model",
        "CRUD endpoints for feeding logs",
        "Filtering by date range",
        "JSON export endpoint"
      ],
      "dependencies": ["PS-001", "PS-002"],
      "technical_notes": "Support multiple babies per parent account"
    },
    {
      "id": "PS-004",
      "title": "Feeding log entry form",
      "priority": "P0",
      "type": "frontend",
      "complexity": "medium",
      "estimated_hours": 6,
      "acceptance_criteria": [
        "Quick-add feeding log form",
        "Food search/autocomplete",
        "Quantity input (oz, ml, servings)",
        "Timestamp picker (defaults to now)",
        "Offline support"
      ],
      "dependencies": ["PS-003"],
      "technical_notes": "PWA offline-first for parents on the go"
    },
    {
      "id": "PS-005",
      "title": "Export feeding history for pediatrician",
      "priority": "P0",
      "type": "backend",
      "complexity": "high",
      "estimated_hours": 8,
      "acceptance_criteria": [
        "Generate PDF report with feeding summary",
        "Include growth charts (if available)",
        "Filter by date range (default: last 30 days)",
        "Email or download options",
        "Professional medical format"
      ],
      "dependencies": ["PS-003"],
      "technical_notes": "Use weasyprint for PDF generation"
    },
    {
      "id": "PS-006",
      "title": "Landing page with product demo",
      "priority": "P0",
      "type": "frontend",
      "complexity": "medium",
      "estimated_hours": 5,
      "acceptance_criteria": [
        "Hero section with value proposition",
        "Feature highlights (3-5 sections)",
        "Screenshot carousel",
        "CTA buttons (Download app)",
        "Responsive design"
      ],
      "dependencies": [],
      "technical_notes": "Use /frontend-design skill for distinctive UI"
    },
    {
      "id": "PS-007",
      "title": "Content library (blog posts, social)",
      "priority": "P1",
      "type": "content",
      "complexity": "low",
      "estimated_hours": 3,
      "acceptance_criteria": [
        "5 blog post outlines",
        "10 social media posts",
        "Email templates (welcome, reminder)",
        "SEO-optimized titles",
        "Spanish language versions"
      ],
      "dependencies": [],
      "technical_notes": "Use /content-library-producer skill"
    }
  ],
  "estimated_total_hours": 37,
  "estimated_timeline": "2 weeks",
  "p0_feature_count": 6,
  "p1_feature_count": 1,
  "p2_feature_count": 0
}
```

## Quality Criteria

A good features.json includes:
- [ ] 5-7 P0 features (MVP core)
- [ ] Clear acceptance criteria for each feature
- [ ] Realistic complexity estimates
- [ ] Proper dependency ordering
- [ ] Technical notes for guidance
- [ ] Compliance requirements identified
- [ ] Success metrics defined
- [ ] Total effort estimate
- [ ] All required metadata

## Feature Schema Reference

```typescript
interface Feature {
  id: string;              // Format: {PROJECT_PREFIX}-{NUMBER}
  title: string;           // Short, actionable title
  priority: "P0" | "P1" | "P2";
  type: "backend" | "frontend" | "infrastructure" | "content";
  complexity: "low" | "medium" | "high";
  estimated_hours: number;
  acceptance_criteria: string[];
  dependencies: string[];  // IDs of prerequisite features
  technical_notes?: string;
}
```

## Example Invocation

```
/mvp-spec-writer

Exploration: babybit-es/explorations/pedi-sync-exploration.md
Project: pedi-sync
Constraints:
  - Backend: FastAPI
  - Auth: JWT with parent accounts
  - Max P0 features: 7
  - Target: 2 weeks completion
  - Must be COPPA compliant
```

## Output

Creates `{domain}/{project}/features.json` ready for Ralph Loop consumption.

## Related Skills

- `/niche-explorer` - Generate exploration report (prerequisite)
- `forge loop run -d <domain> -p <project>` - Execute features.json
- `/living-docs update` - Document the new project
