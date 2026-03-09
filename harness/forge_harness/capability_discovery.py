"""Capability Discovery for FORGE Agents.

Suggests the best agent for a given task based on role capabilities and skills.
"""

import re
from dataclasses import dataclass, field


@dataclass
class AgentCapability:
    """Capability profile for an agent role."""

    role: str
    name: str
    description: str
    skills: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)  # Preferred domains


# Agent capability registry
AGENT_CAPABILITIES: list[AgentCapability] = [
    AgentCapability(
        role="backend-engineer",
        name="Backend Engineer",
        description="API development, database design, business logic, server-side code",
        skills=[
            "python",
            "fastapi",
            "django",
            "flask",
            "postgresql",
            "redis",
            "mongodb",
            "sqlite",
            "api",
            "rest",
            "graphql",
            "grpc",
            "authentication",
            "authorization",
            "jwt",
            "oauth",
            "async",
            "celery",
            "background-tasks",
            "testing",
            "pytest",
            "unittest",
        ],
        keywords=[
            "api",
            "endpoint",
            "database",
            "db",
            "query",
            "model",
            "backend",
            "server",
            "service",
            "microservice",
            "auth",
            "login",
            "signup",
            "jwt",
            "token",
            "session",
            "crud",
            "create",
            "read",
            "update",
            "delete",
            "migrate",
            "migration",
            "schema",
            "table",
            "cache",
            "redis",
            "performance",
            "optimize",
            "webhook",
            "integration",
            "third-party",
            "business logic",
            "validation",
            "error handling",
        ],
    ),
    AgentCapability(
        role="frontend-builder",
        name="Frontend Builder",
        description="UI components, React/Vue/Lit, responsive design, user experience",
        skills=[
            "react",
            "vue",
            "lit",
            "svelte",
            "typescript",
            "javascript",
            "html",
            "css",
            "tailwind",
            "styled-components",
            "sass",
            "state-management",
            "redux",
            "zustand",
            "context",
            "pwa",
            "service-worker",
            "offline",
            "accessibility",
            "a11y",
            "aria",
            "responsive",
            "mobile-first",
        ],
        keywords=[
            "ui",
            "component",
            "page",
            "screen",
            "view",
            "frontend",
            "client",
            "browser",
            "dom",
            "button",
            "form",
            "input",
            "modal",
            "dialog",
            "style",
            "css",
            "layout",
            "grid",
            "flexbox",
            "responsive",
            "mobile",
            "desktop",
            "tablet",
            "animation",
            "transition",
            "hover",
            "click",
            "user experience",
            "ux",
            "usability",
            "react",
            "vue",
            "lit",
            "component",
            "state",
            "props",
            "hooks",
            "context",
        ],
    ),
    AgentCapability(
        role="qa-test-guardian",
        name="QA Test Guardian",
        description="Testing, coverage, E2E tests, quality assurance",
        skills=[
            "pytest",
            "jest",
            "vitest",
            "playwright",
            "cypress",
            "unit-testing",
            "integration-testing",
            "e2e-testing",
            "test-coverage",
            "mocking",
            "fixtures",
            "ci-cd",
            "github-actions",
            "automation",
            "regression",
            "smoke-testing",
        ],
        keywords=[
            "test",
            "testing",
            "spec",
            "coverage",
            "unit",
            "integration",
            "e2e",
            "end-to-end",
            "assert",
            "expect",
            "mock",
            "stub",
            "fixture",
            "regression",
            "smoke",
            "sanity",
            "quality",
            "qa",
            "bug",
            "defect",
            "ci",
            "cd",
            "pipeline",
            "automation",
            "playwright",
            "cypress",
            "selenium",
        ],
    ),
    AgentCapability(
        role="security-auditor",
        name="Security Auditor",
        description="Security review, vulnerability assessment, OWASP compliance",
        skills=[
            "owasp",
            "security",
            "penetration-testing",
            "authentication",
            "authorization",
            "encryption",
            "xss",
            "csrf",
            "sql-injection",
            "injection",
            "secrets-management",
            "vault",
            "env-vars",
            "compliance",
            "gdpr",
            "hipaa",
            "soc2",
        ],
        keywords=[
            "security",
            "secure",
            "vulnerability",
            "vuln",
            "audit",
            "review",
            "scan",
            "assessment",
            "owasp",
            "injection",
            "xss",
            "csrf",
            "sqli",
            "authentication",
            "authorization",
            "permission",
            "encryption",
            "hash",
            "salt",
            "secret",
            "credential",
            "password",
            "token",
            "key",
            "compliance",
            "gdpr",
            "hipaa",
            "pci",
            "penetration",
            "pentest",
            "exploit",
        ],
    ),
    AgentCapability(
        role="devops-deployer",
        name="DevOps Deployer",
        description="Docker, CI/CD, deployment, infrastructure",
        skills=[
            "docker",
            "kubernetes",
            "helm",
            "github-actions",
            "gitlab-ci",
            "jenkins",
            "terraform",
            "pulumi",
            "cloudformation",
            "aws",
            "gcp",
            "azure",
            "railway",
            "vercel",
            "nginx",
            "caddy",
            "load-balancing",
            "monitoring",
            "logging",
            "alerting",
        ],
        keywords=[
            "deploy",
            "deployment",
            "release",
            "docker",
            "container",
            "kubernetes",
            "k8s",
            "ci",
            "cd",
            "pipeline",
            "workflow",
            "infrastructure",
            "infra",
            "server",
            "cloud",
            "aws",
            "gcp",
            "azure",
            "railway",
            "vercel",
            "nginx",
            "proxy",
            "load-balancer",
            "monitoring",
            "logging",
            "metrics",
            "alert",
            "environment",
            "staging",
            "production",
            "scale",
            "scaling",
            "replica",
        ],
    ),
    AgentCapability(
        role="debug-detective",
        name="Debug Detective",
        description="Complex bugs, race conditions, memory leaks, intermittent failures",
        skills=[
            "debugging",
            "profiling",
            "tracing",
            "race-conditions",
            "concurrency",
            "deadlocks",
            "memory-leaks",
            "heap-analysis",
            "logging",
            "instrumentation",
            "root-cause-analysis",
        ],
        keywords=[
            "bug",
            "debug",
            "fix",
            "issue",
            "problem",
            "error",
            "exception",
            "crash",
            "failure",
            "intermittent",
            "flaky",
            "random",
            "sometimes",
            "race",
            "condition",
            "concurrent",
            "deadlock",
            "memory",
            "leak",
            "heap",
            "stack",
            "trace",
            "traceback",
            "log",
            "investigate",
            "root cause",
            "why",
            "reproduce",
        ],
    ),
    AgentCapability(
        role="architect-advisor",
        name="Architect Advisor",
        description="System design, architecture decisions, technical strategy",
        skills=[
            "system-design",
            "architecture",
            "patterns",
            "microservices",
            "monolith",
            "modular",
            "event-driven",
            "cqrs",
            "event-sourcing",
            "api-design",
            "data-modeling",
            "scalability",
            "reliability",
            "maintainability",
        ],
        keywords=[
            "architecture",
            "design",
            "pattern",
            "structure",
            "decision",
            "tradeoff",
            "compare",
            "choose",
            "microservice",
            "monolith",
            "modular",
            "scale",
            "scalability",
            "performance",
            "maintain",
            "maintainability",
            "clean",
            "refactor",
            "restructure",
            "reorganize",
            "system",
            "component",
            "module",
            "layer",
            "api",
            "interface",
            "contract",
            "data model",
            "schema",
            "entity",
        ],
    ),
    AgentCapability(
        role="performance-optimizer",
        name="Performance Optimizer",
        description="Profiling, bottleneck identification, optimization",
        skills=[
            "profiling",
            "benchmarking",
            "optimization",
            "caching",
            "indexing",
            "query-optimization",
            "load-testing",
            "stress-testing",
            "memory-optimization",
            "cpu-optimization",
            "database-tuning",
            "network-optimization",
        ],
        keywords=[
            "performance",
            "slow",
            "fast",
            "speed",
            "optimize",
            "optimization",
            "improve",
            "profile",
            "profiling",
            "benchmark",
            "bottleneck",
            "latency",
            "throughput",
            "cache",
            "caching",
            "memoize",
            "index",
            "indexing",
            "query",
            "load",
            "stress",
            "concurrent",
            "memory",
            "cpu",
            "resource",
            "n+1",
            "batch",
            "bulk",
        ],
    ),
    AgentCapability(
        role="content-agent",
        name="Content Agent",
        description="Blog posts, documentation, social media, marketing copy",
        skills=[
            "writing",
            "copywriting",
            "editing",
            "seo",
            "keywords",
            "meta-tags",
            "markdown",
            "documentation",
            "social-media",
            "twitter",
            "linkedin",
            "email",
            "newsletter",
        ],
        keywords=[
            "content",
            "blog",
            "post",
            "article",
            "documentation",
            "docs",
            "readme",
            "copy",
            "copywriting",
            "marketing",
            "social",
            "twitter",
            "linkedin",
            "thread",
            "email",
            "newsletter",
            "campaign",
            "seo",
            "keyword",
            "meta",
            "write",
            "writing",
            "draft",
        ],
    ),
]


@dataclass
class Suggestion:
    """A suggested agent for a task."""

    role: str
    name: str
    confidence: float  # 0.0 to 1.0
    matching_skills: list[str]
    matching_keywords: list[str]
    reason: str


def suggest_agent(
    task: str,
    available_roles: list[str] | None = None,
    domain: str | None = None,
    top_n: int = 3,
) -> list[Suggestion]:
    """Suggest the best agent(s) for a task.

    Args:
        task: Description of the task
        available_roles: Limit to these roles (None = all)
        domain: Prefer agents suited for this domain
        top_n: Number of suggestions to return

    Returns:
        List of suggestions ordered by confidence
    """
    task_lower = task.lower()
    suggestions = []

    for cap in AGENT_CAPABILITIES:
        # Skip if not in available roles
        if available_roles and cap.role not in available_roles:
            continue

        score = 0.0
        matching_skills = []
        matching_keywords = []

        # Check keyword matches (weighted higher)
        for keyword in cap.keywords:
            if keyword in task_lower:
                score += 2.0
                matching_keywords.append(keyword)

        # Check skill matches
        for skill in cap.skills:
            if skill in task_lower:
                score += 1.5
                matching_skills.append(skill)

        # Check word boundaries for better matching
        words = re.findall(r"\b\w+\b", task_lower)
        for word in words:
            if word in cap.keywords and word not in matching_keywords:
                score += 1.0
                matching_keywords.append(word)
            if word in cap.skills and word not in matching_skills:
                score += 0.75
                matching_skills.append(word)

        # Domain bonus
        if domain and domain in cap.domains:
            score += 1.0

        if score > 0:
            # Normalize confidence (max reasonable score ~10-15)
            confidence = min(score / 10.0, 1.0)

            # Generate reason
            reasons = []
            if matching_keywords:
                reasons.append(f"matches keywords: {', '.join(matching_keywords[:3])}")
            if matching_skills:
                reasons.append(f"has skills: {', '.join(matching_skills[:3])}")

            suggestions.append(
                Suggestion(
                    role=cap.role,
                    name=cap.name,
                    confidence=confidence,
                    matching_skills=matching_skills,
                    matching_keywords=matching_keywords,
                    reason="; ".join(reasons) if reasons else cap.description,
                )
            )

    # Sort by confidence
    suggestions.sort(key=lambda s: s.confidence, reverse=True)

    return suggestions[:top_n]


def get_agent_info(role: str) -> AgentCapability | None:
    """Get capability info for a role."""
    for cap in AGENT_CAPABILITIES:
        if cap.role == role:
            return cap
    return None


def list_all_roles() -> list[str]:
    """List all available agent roles."""
    return [cap.role for cap in AGENT_CAPABILITIES]


def format_suggestion(suggestion: Suggestion) -> str:
    """Format a suggestion for display."""
    confidence_bar = "█" * int(suggestion.confidence * 10) + "░" * (
        10 - int(suggestion.confidence * 10)
    )
    return (
        f"{suggestion.name} ({suggestion.role})\n"
        f"  Confidence: [{confidence_bar}] {suggestion.confidence:.0%}\n"
        f"  Reason: {suggestion.reason}"
    )
