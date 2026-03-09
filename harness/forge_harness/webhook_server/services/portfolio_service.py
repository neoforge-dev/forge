"""
Portfolio Service

Service for reading portfolio data from domains.yaml and features.json.
"""

import json
import os
import subprocess
import time
from pathlib import Path

from forge_harness.logging_config import get_logger
from forge_harness.webhook_server.services.agent_registry import get_agent_registry

logger = get_logger(__name__)


def _count_pending_approvals() -> int:
    """Return the number of pending approval requests by reading storage directly.

    Reads the JSON approval files synchronously so it can be called from
    non-async contexts (e.g. PortfolioService.get_project_details).  Returns 0
    if the approval storage directory is unavailable or unreadable.
    """
    try:
        storage_dir_env = os.environ.get("FORGE_APPROVALS_DIR")
        if storage_dir_env:
            storage_dir = Path(storage_dir_env)
        else:
            forge_root = os.environ.get("FORGE_ROOT")
            if forge_root:
                storage_dir = Path(forge_root) / ".forge/approvals"
            else:
                storage_dir = Path(".forge/approvals")

        if not storage_dir.is_dir():
            return 0

        pending_count = 0
        for json_file in storage_dir.glob("approval_*.json"):
            try:
                with open(json_file) as fh:
                    data = json.load(fh)
                if data.get("status") == "pending":
                    pending_count += 1
            except (OSError, json.JSONDecodeError, KeyError):
                continue
        return pending_count
    except Exception as exc:
        logger.debug(f"Could not read pending approvals count: {exc}")
        return 0


class PortfolioService:
    """Service for reading portfolio data from domains.yaml and features.json."""

    def __init__(self, forge_root: Path | None = None):
        """Initialize portfolio service.

        Args:
            forge_root: Root path of FORGE repository
        """
        self._forge_root = forge_root or self._find_forge_root()
        self._domains_cache: dict | None = None
        self._cache_time: float = 0
        self._cache_ttl = 60  # Cache for 60 seconds

    def _find_forge_root(self) -> Path:
        """Find FORGE root directory."""
        # Try to find by looking for domains.yaml
        current = Path(__file__).parent
        while current != current.parent:
            if (current / "domains.yaml").exists():
                return current
            if (current / "forge_harness" / "domains.yaml").exists():
                return current
            current = current.parent
        # Default to parent of this file
        return Path(__file__).parent.parent

    def _load_domains(self) -> dict:
        """Load domains.yaml with caching."""
        now = time.time()
        if self._domains_cache and (now - self._cache_time) < self._cache_ttl:
            return self._domains_cache

        domains_path = self._forge_root / "forge_harness" / "domains.yaml"
        if not domains_path.exists():
            domains_path = self._forge_root / "domains.yaml"

        if not domains_path.exists():
            logger.warning(f"domains.yaml not found at {domains_path}")
            return {"domains": {}}

        try:
            import yaml

            with open(domains_path) as f:
                self._domains_cache = yaml.safe_load(f)
                self._cache_time = now
                return self._domains_cache
        except Exception as e:
            logger.error(f"Error loading domains.yaml: {e}")
            return {"domains": {}}

    def _load_features(self, domain: str, project: str) -> dict | None:
        """Load features.json for a project."""
        # Check multiple possible locations
        possible_paths = [
            self._forge_root / domain / project / "features.json",
            self._forge_root.parent / domain / project / "features.json",
            self._forge_root / project / "features.json",
        ]

        for path in possible_paths:
            if path.exists():
                try:
                    import json

                    with open(path) as f:
                        return json.load(f)
                except Exception as e:
                    logger.error(f"Error loading {path}: {e}")
                    return None
        return None

    def _count_feature_status(self, features: dict | None) -> dict[str, int]:
        """Count features by status."""
        if not features or "features" not in features:
            return {"total": 0, "passing": 0, "failing": 0, "pending": 0, "blocked": 0}

        counts = {"total": 0, "passing": 0, "failing": 0, "pending": 0, "blocked": 0}
        for f in features.get("features", []):
            counts["total"] += 1
            status = f.get("status", "pending").lower()
            if status in counts:
                counts[status] += 1
            else:
                counts["pending"] += 1
        return counts

    def _get_tech_stack(self, domain_id: str, project_slug: str, domain_info: dict) -> list[str]:
        """Get tech stack from features.json or domain frontend tier."""
        features_data = self._load_features(domain_id, project_slug)
        if features_data and isinstance(features_data.get("tech_stack"), list):
            return list(features_data["tech_stack"])

        frontend_tier = (domain_info or {}).get("frontend_tier", "") or ""
        if not frontend_tier:
            return []

        tier_map = {
            "React": ["React", "TypeScript", "FastAPI", "PostgreSQL"],
            "Lit PWA": ["Lit", "Web Components", "FastAPI", "PostgreSQL"],
            "React Native": ["React Native", "TypeScript", "FastAPI", "PostgreSQL"],
        }
        if frontend_tier in tier_map:
            return tier_map[frontend_tier]

        return [frontend_tier, "FastAPI", "PostgreSQL"]

    def get_all_projects(self) -> list[dict]:
        """Get all projects across all domains with their status.

        Returns a list of projects with fields:
        - domain: Domain ID
        - project: Project slug
        - display_name: Human-readable name
        - status: Project status (live, ready, dev, parked, blocked)
        - progress_pct: Progress percentage
        """
        domains_data = self._load_domains()
        domains = domains_data.get("domains", {})

        projects = []
        for domain_id, domain_info in domains.items():
            if not domain_info.get("active", False):
                continue

            for product in domain_info.get("products", []):
                # Load features to get actual status
                features = self._load_features(domain_id, product)

                # Determine status from features
                status = "dev"  # Default
                progress_pct = 0

                if features:
                    feature_counts = self._count_feature_status(features)
                    total = feature_counts.get("total", 0)
                    if total > 0:
                        passing = feature_counts.get("passing", 0)
                        progress_pct = int((passing / total) * 100)

                        if progress_pct == 100:
                            status = "live"
                        elif progress_pct >= 80:
                            status = "ready"
                        elif progress_pct >= 50:
                            status = "dev"
                        else:
                            status = "parked"

                projects.append(
                    {
                        "domain": domain_id,
                        "project": product,
                        "display_name": product.replace("-", " ").replace("_", " ").title(),
                        "status": status,
                        "progress_pct": progress_pct,
                    }
                )

        return projects

    def get_portfolio_summary(self) -> dict:
        """Get portfolio summary with summary and projects list."""
        # Get all projects
        projects = self.get_all_projects()

        # Count by status
        by_status = {"live": 0, "ready": 0, "dev": 0, "parked": 0, "blocked": 0}
        for project in projects:
            status = project.get("status", "dev")
            if status in by_status:
                by_status[status] += 1
            else:
                by_status["dev"] += 1  # Default to dev if unknown status

        # Count active agents from ALL sources (registry, state_store, tmux)
        # This matches the logic in /api/agents endpoint for consistency
        active_agents_count = 0
        seen_ids = set()

        try:
            # 1. Count from registry
            registry = get_agent_registry()
            registry_agents = registry.list_active()
            for agent in registry_agents:
                seen_ids.add(agent.id)
                active_agents_count += 1
            logger.debug(f"Portfolio: Counted {len(registry_agents)} agents from registry")
        except Exception as e:
            logger.warning(f"Failed to get agents from registry: {e}")

        try:
            # 2. Count from state_store (if not already counted)
            # We import StateStore locally to avoid circular dependency
            from forge_harness.state_store import StateStore

            state_store = StateStore()
            state_store.connect()
            if state_store.is_connected():
                store_agents = state_store.get_active_agents()
                for sa in store_agents:
                    if sa.session_id not in seen_ids:
                        seen_ids.add(sa.session_id)
                        active_agents_count += 1
                logger.debug(
                    f"Portfolio: Added agents from state_store, total now {active_agents_count}"
                )
        except Exception as e:
            logger.warning(f"Failed to get agents from state_store: {e}")

        try:
            # 3. Count from tmux sessions (if not already counted)
            # We import SessionTracker locally to avoid circular dependency
            try:
                from forge_harness.session_tracker import SessionTracker

                tracker = SessionTracker()
                sessions = tracker.get_all_sessions()
                for s in sessions:
                    if s.session_name not in seen_ids and s.window_name not in seen_ids:
                        seen_ids.add(s.session_name)
                        active_agents_count += 1
                logger.debug(f"Portfolio: Added tmux sessions, total now {active_agents_count}")
            except ImportError:
                pass  # SessionTracker might not be available in all contexts
        except Exception as e:
            logger.warning(f"Failed to get tmux sessions: {e}")

        # Count pending approvals
        pending_approvals_count = 0
        try:
            # Count from .forge/approvals directory
            approvals_dir = Path(self._forge_root / ".forge/approvals")
            if approvals_dir.exists():
                pending_approvals_count = len(list(approvals_dir.glob("*.json")))
        except Exception as e:
            logger.warning(f"Failed to get pending approvals count: {e}")

        # Build summary
        summary = {
            "total_projects": len(projects),
            "by_status": by_status,
            "active_agents": active_agents_count,
            "pending_approvals": pending_approvals_count,
        }

        return {
            "summary": summary,
            "projects": projects,
        }

    def get_domain_projects(self, domain_id: str) -> dict | None:
        """Get projects in a domain."""
        domains_data = self._load_domains()
        domains = domains_data.get("domains", {})

        if domain_id not in domains:
            return None

        domain_info = domains[domain_id]
        products = domain_info.get("products", [])

        projects = []
        for product in products:
            # Convert product name to project slug
            project_slug = product.lower().replace(" ", "-")
            features = self._load_features(domain_id, project_slug)
            feature_counts = self._count_feature_status(features)

            # Determine project status based on features
            if feature_counts["blocked"] > 0:
                status = "blocked"
            elif feature_counts["failing"] > 0:
                status = "failing"
            elif feature_counts["pending"] > 0:
                status = "dev"
            elif feature_counts["passing"] > 0:
                status = "ready"
            else:
                status = "pending"

            projects.append(
                {
                    "name": product,
                    "slug": project_slug,
                    "domain": domain_id,
                    "status": status,
                    "features": feature_counts,
                }
            )

        # Sort by status priority
        status_priority = {"blocked": 0, "failing": 1, "dev": 2, "pending": 3, "ready": 4}
        projects.sort(key=lambda p: status_priority.get(p["status"], 5))

        return {
            "domain": {
                "id": domain_id,
                "display_name": domain_info.get("display_name", domain_id),
                "compliance": domain_info.get("compliance", []),
                "human_gates": domain_info.get("human_gates", []),
                "content_tier": domain_info.get("content", {}).get("tier", 3),
            },
            "projects": projects,
            "count": len(projects),
        }

    def get_project_details(self, domain_id: str, project_slug: str) -> dict | None:
        """Get detailed project information."""

        domains_data = self._load_domains()
        domains = domains_data.get("domains", {})

        if domain_id not in domains:
            return None

        domain_info = domains[domain_id]
        products = domain_info.get("products", [])

        # Find matching product
        product_name = None
        for product in products:
            if product.lower().replace(" ", "-") == project_slug:
                product_name = product
                break

        if not product_name:
            return None

        # Load features
        features_data = self._load_features(domain_id, project_slug)
        feature_counts = self._count_feature_status(features_data)

        # Get features list
        features_list = []
        if features_data and "features" in features_data:
            for f in features_data["features"]:
                features_list.append(
                    {
                        "id": f.get("id"),
                        "name": f.get("name"),
                        "status": f.get("status", "pending"),
                        "priority": f.get("priority", "P2"),
                    }
                )

        # Determine project status
        if feature_counts["blocked"] > 0:
            status = "blocked"
        elif feature_counts["failing"] > 0:
            status = "failing"
        elif feature_counts["pending"] > 0:
            status = "dev"
        elif feature_counts["passing"] > 0:
            status = "ready"
        else:
            status = "pending"

        # Try to get recent git commits
        recent_commits = []
        project_path = self._forge_root.parent / domain_id / project_slug
        if project_path.exists():
            try:
                result = subprocess.run(
                    ["git", "log", "--oneline", "-5"],
                    cwd=project_path,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if result.returncode == 0:
                    for line in result.stdout.strip().split("\n"):
                        if line:
                            parts = line.split(" ", 1)
                            if len(parts) == 2:
                                recent_commits.append(
                                    {
                                        "hash": parts[0],
                                        "message": parts[1],
                                    }
                                )
            except Exception:
                pass

        # Check for active agent
        agent_registry = get_agent_registry()
        active_agents = [
            a.to_dict()
            for a in agent_registry.list_active()
            if f"{domain_id}/{project_slug}" in a.project or project_slug in a.project
        ]

        # Get deployment info
        deployment = domain_info.get("deployment", {})
        production_url = None
        if deployment.get("cloudflare_project"):
            production_url = f"https://{deployment['cloudflare_project']}.pages.dev"

        # Derive tech stack from domain configuration or features.json
        tech_stack = self._get_tech_stack(domain_id, project_slug, domain_info)

        return {
            "name": product_name,
            "slug": project_slug,
            "domain": domain_id,
            "status": status,
            "features": {
                "counts": feature_counts,
                "list": features_list,
            },
            "recent_commits": recent_commits,
            "active_agents": active_agents,
            "pending_approvals_count": _count_pending_approvals(),
            "production_url": production_url,
            "compliance": domain_info.get("compliance", []),
            "human_gates": domain_info.get("human_gates", []),
            "tech_stack": tech_stack,
        }


# Global portfolio service
_portfolio_service: PortfolioService | None = None


def get_portfolio_service() -> PortfolioService:
    """Get or create global portfolio service."""
    global _portfolio_service
    if _portfolio_service is None:
        _portfolio_service = PortfolioService()
    return _portfolio_service
