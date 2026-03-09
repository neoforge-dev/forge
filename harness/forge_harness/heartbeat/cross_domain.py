"""Cross-domain heartbeat collector for FORGE agent sessions."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from forge_harness.domain_registry import DomainRegistry
from forge_harness.logging_config import get_logger
from forge_harness.session_tracker import SessionTracker

logger = get_logger(__name__)


class CrossDomainHeartbeatCollector:
    """Collect heartbeat summaries across all domains."""

    def __init__(
        self,
        forge_root: Path | None = None,
        sessions_dir: Path | None = None,
        output_dir: Path | None = None,
        session_prefix: str = "forge",
    ) -> None:
        self.forge_root = (forge_root or Path.cwd()).resolve()
        self.sessions_dir = sessions_dir or (self.forge_root / ".forge/sessions")
        self.output_dir = output_dir or (self.forge_root / ".forge" / "heartbeat")
        self.session_prefix = session_prefix
        self._tracker = SessionTracker(session_prefix=session_prefix)
        # Ensure tracker uses repository-level sessions dir
        self._tracker.sessions_dir = self.sessions_dir

    def collect(self) -> dict[str, dict[str, Any]]:
        """Collect heartbeats and write per-domain snapshots.

        Returns:
            Mapping of domain -> summary dict
        """
        domains = self._load_domains()
        all_sessions = self._tracker.get_all_sessions()

        grouped: dict[str, list[Any]] = {domain: [] for domain in domains}
        for session in all_sessions:
            if session.domain and session.domain in grouped:
                grouped[session.domain].append(session)

        summaries: dict[str, dict[str, Any]] = {}
        for domain in domains:
            summary = self._build_domain_summary(domain, grouped.get(domain, []))
            summaries[domain] = summary
            self._write_domain_summary(domain, summary)

        return summaries

    def _load_domains(self) -> list[str]:
        """Load domain keys from DomainRegistry."""
        try:
            domains_yaml = self.forge_root / "harness" / "forge_harness" / "domains.yaml"
            if domains_yaml.exists():
                DomainRegistry.load(domains_yaml)
            else:
                DomainRegistry.load()
            return DomainRegistry.keys()
        except Exception as exc:
            logger.warning(f"Failed to load domains.yaml: {exc}")
            return []

    def _build_domain_summary(self, domain: str, sessions: list[Any]) -> dict[str, Any]:
        now = datetime.now(UTC)
        payload = [asdict(session) for session in sessions]
        status_counts = {
            "active": sum(1 for session in sessions if session.status == "active"),
            "idle": sum(1 for session in sessions if session.status == "idle"),
            "error": sum(1 for session in sessions if session.status == "error"),
            "unknown": sum(1 for session in sessions if session.status == "unknown"),
        }

        return {
            "domain": domain,
            "timestamp": now.isoformat(),
            "total_sessions": len(sessions),
            "status_counts": status_counts,
            "sessions": payload,
        }

    def _write_domain_summary(self, domain: str, summary: dict[str, Any]) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        output_path = self.output_dir / f"{domain}.json"
        output_path.write_text(json.dumps(summary, indent=2, default=str))
