"""Code Atlas integration for Tech Diligence prioritization.

This module enhances Tech Diligence findings with historical insights from Code Atlas:
- Historical success filtering: Skip features for problems already solved
- Incremental scan targeting: Scope scans to recently-changed files
- File hotspot prioritization: Boost priority for frequently-changed files
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


class AtlasPrioritizer:
    """Enhances Tech Diligence with Code Atlas insights."""

    def __init__(self, code_atlas_bridge):
        """Initialize with Code Atlas bridge.

        Args:
            code_atlas_bridge: CodeAtlasBridge instance
        """
        self.atlas = code_atlas_bridge
        self._available: bool | None = None
        self._hotspot_cache: dict[str, int] | None = None

    async def is_available(self) -> bool:
        """Check if Code Atlas is indexed and queryable.

        Returns:
            True if Code Atlas is available, False otherwise
        """
        if self._available is None:
            try:
                # Quick health check
                healthy = await self.atlas.health_check()
                self._available = healthy and self.atlas.enabled

                if self._available:
                    logger.info("Code Atlas is available and healthy")
                else:
                    logger.debug("Code Atlas is not available or disabled")

            except Exception as e:
                logger.debug(f"Code Atlas availability check failed: {e}")
                self._available = False

        return self._available

    async def filter_by_historical_success(
        self,
        findings: list[dict[str, Any]],
        min_confidence: float = 0.5
    ) -> list[dict[str, Any]]:
        """Filter findings based on past solution success.

        Queries Code Atlas for similar problems that were previously solved.
        Enhances findings with past solution metadata and filters out
        problems that have no past solutions (potentially novel issues).

        Args:
            findings: List of Tech Diligence findings
            min_confidence: Minimum confidence score for past solutions

        Returns:
            Enhanced findings with historical context
        """
        if not await self.is_available():
            logger.debug("Code Atlas not available, skipping historical filtering")
            return findings

        enhanced = []
        skipped_count = 0

        for finding in findings:
            # Query for past solutions
            past_solutions = await self._query_past_solutions(
                finding,
                min_confidence=min_confidence
            )

            if past_solutions:
                # Add metadata about past solutions
                if "metadata" not in finding:
                    finding["metadata"] = {}

                finding["metadata"]["past_solutions"] = past_solutions
                finding["metadata"]["solution_count"] = len(past_solutions)

                # Add summary to description
                solution_summary = ", ".join([
                    s.get("solution", "Unknown")
                    for s in past_solutions[:3]
                ])
                finding["description"] = (
                    f"{finding.get('description', '')}\n\n"
                    f"**Past Solutions ({len(past_solutions)})**: {solution_summary}"
                )

                enhanced.append(finding)
            else:
                # No past solutions found - could be novel problem
                # Keep it but mark as potentially novel
                if "metadata" not in finding:
                    finding["metadata"] = {}
                finding["metadata"]["novel_problem"] = True
                enhanced.append(finding)

        logger.info(
            f"Historical filtering: {len(findings)} findings → "
            f"{len(enhanced)} enhanced ({skipped_count} skipped)"
        )

        return enhanced

    async def _query_past_solutions(
        self,
        finding: dict[str, Any],
        min_confidence: float = 0.5
    ) -> list[dict[str, str]]:
        """Query Code Atlas for similar past solutions.

        Args:
            finding: Tech Diligence finding dict
            min_confidence: Minimum confidence score

        Returns:
            List of past solution dicts with problem/solution names
        """
        try:
            # Build query from finding category and description
            category = finding.get("category", "")
            title = finding.get("title", "")
            description = finding.get("description", "")

            # Combine into search query
            query = f"{category} {title} {description}"[:200]  # Limit length

            # Search for similar problems
            results = await self.atlas.search_similar_problems(
                problem_description=query,
                limit=5
            )

            # Filter by confidence and extract solution info
            solutions = []
            for result in results:
                score = result.get("combined_score", 0.0)
                if score >= min_confidence:
                    solutions.append({
                        "problem": result.get("entity_name", "Unknown"),
                        "solution": result.get("properties", {}).get("solution", "See Atlas"),
                        "confidence": score,
                        "entity_id": result.get("entity_id", "")
                    })

            return solutions

        except Exception as e:
            logger.warning(f"Failed to query past solutions: {e}")
            return []

    async def get_recently_changed_files(
        self,
        domain: str,
        project: str,
        days_back: int = 7,
        limit: int = 100
    ) -> list[str]:
        """Get list of recently-changed files from Code Atlas.

        Uses session data to identify files mentioned in recent Claude sessions.
        This enables incremental scans that only check actively-worked-on files.

        Args:
            domain: Domain name (e.g., "codeswiftr-com")
            project: Project name (e.g., "interview-simulator")
            days_back: How many days back to look
            limit: Maximum number of files to return

        Returns:
            List of file paths relative to project root
        """
        if not await self.is_available():
            logger.debug("Code Atlas not available, cannot get recently changed files")
            return []

        try:
            # Get signals which includes file mentions
            signals = await self.atlas.get_signals(
                domain=domain,
                project=project
            )

            # Extract file paths from patterns
            file_paths = set()
            for pattern in signals.related_patterns:
                # Patterns include entities which may be files
                for entity in pattern.entities:
                    if "/" in entity or entity.endswith((".py", ".ts", ".tsx", ".js")):
                        file_paths.add(entity)

            result = list(file_paths)[:limit]
            logger.info(f"Found {len(result)} recently changed files from Code Atlas")
            return result

        except Exception as e:
            logger.warning(f"Failed to get recently changed files: {e}")
            return []

    async def get_file_hotspots(
        self,
        domain: str,
        project: str,
        min_mentions: int = 3
    ) -> dict[str, int]:
        """Get file hotspots (frequently mentioned files).

        Files mentioned across many sessions are likely hotspots:
        - High complexity (frequently changed)
        - Critical paths (important functionality)
        - Technical debt accumulation points

        Args:
            domain: Domain name
            project: Project name
            min_mentions: Minimum mentions to be considered a hotspot

        Returns:
            Dict mapping file paths to mention counts
        """
        if not await self.is_available():
            logger.debug("Code Atlas not available, cannot get file hotspots")
            return {}

        # Check cache first
        if self._hotspot_cache is not None:
            return self._hotspot_cache

        try:
            # Search for File entities with mention counts
            # Note: This might require custom Code Atlas API endpoint
            # For now, use entity search and count from session signals

            signals = await self.atlas.get_signals(
                domain=domain,
                project=project
            )

            # Count file mentions across patterns
            file_counts: dict[str, int] = {}
            for pattern in signals.related_patterns:
                for entity in pattern.entities:
                    if "/" in entity or entity.endswith((".py", ".ts", ".tsx", ".js")):
                        file_counts[entity] = file_counts.get(entity, 0) + pattern.session_count

            # Filter by minimum mentions
            hotspots = {
                file: count
                for file, count in file_counts.items()
                if count >= min_mentions
            }

            # Cache results
            self._hotspot_cache = hotspots

            logger.info(f"Identified {len(hotspots)} file hotspots")
            return hotspots

        except Exception as e:
            logger.warning(f"Failed to get file hotspots: {e}")
            return {}

    async def boost_hotspot_priorities(
        self,
        findings: list[dict[str, Any]],
        domain: str,
        project: str,
        boost_threshold: int = 5
    ) -> list[dict[str, Any]]:
        """Boost priority for findings in hotspot files.

        Findings in frequently-changed files are more impactful than
        findings in stable utility files.

        Args:
            findings: List of Tech Diligence findings
            domain: Domain name
            project: Project name
            boost_threshold: Minimum mentions to boost priority

        Returns:
            Findings with adjusted priorities
        """
        if not await self.is_available():
            logger.debug("Code Atlas not available, skipping hotspot prioritization")
            return findings

        # Get hotspot data
        hotspots = await self.get_file_hotspots(domain, project)
        if not hotspots:
            logger.debug("No hotspots found, skipping boost")
            return findings

        boosted_count = 0
        for finding in findings:
            file_path = finding.get("file_path", "")
            if not file_path:
                continue

            # Check if file is a hotspot
            mentions = hotspots.get(file_path, 0)
            if mentions >= boost_threshold:
                # Boost severity/priority
                current_severity = finding.get("severity", "medium")
                severity_order = ["low", "medium", "high", "critical"]

                if current_severity in severity_order:
                    current_idx = severity_order.index(current_severity)
                    # Boost one level (but not past critical)
                    new_idx = min(current_idx + 1, len(severity_order) - 1)
                    finding["severity"] = severity_order[new_idx]

                    # Add metadata
                    if "metadata" not in finding:
                        finding["metadata"] = {}
                    finding["metadata"]["hotspot_mentions"] = mentions
                    finding["metadata"]["priority_boosted"] = True

                    boosted_count += 1

        logger.info(f"Boosted priority for {boosted_count} findings in hotspot files")
        return findings

    def clear_cache(self) -> None:
        """Clear cached hotspot data."""
        self._hotspot_cache = None
        logger.debug("Cleared AtlasPrioritizer cache")
