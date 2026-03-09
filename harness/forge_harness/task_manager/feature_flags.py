from __future__ import annotations

"""Feature flags for V2/V3 shadow deploy.

This module provides a small YAML-backed feature flag helper focused on the
V2/V3 transition use cases:

- Global feature flags under ``features``.
- Per-node overrides under ``node_overrides``.

Configuration is stored in ``.forge/config/features.yaml`` relative to the
project root. The helpers here are intentionally simple and reload from disk
on each mutation so changes take effect without restarting long-running
processes.
"""

from pathlib import Path
from typing import Any

import yaml


class FeatureFlags:
    """YAML-backed feature flag helper with node-specific overrides."""

    def __init__(self, config_path: Path | None = None) -> None:
        self.config_path: Path = config_path or Path(".forge/config/features.yaml")
        self.config: dict[str, Any] = self._load()

    # --------------------------------------------------------------------- #
    # Internal helpers
    # --------------------------------------------------------------------- #

    def _load(self) -> dict[str, Any]:
        """Load configuration from YAML, ensuring required keys exist."""
        if self.config_path.exists():
            raw = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
            if not isinstance(raw, dict):
                raw = {}
        else:
            raw = {}

        raw.setdefault("features", {})
        raw.setdefault("node_overrides", {})

        # Normalise shapes
        if not isinstance(raw["features"], dict):
            raw["features"] = {}
        if not isinstance(raw["node_overrides"], dict):
            raw["node_overrides"] = {}

        return raw

    def _save(self) -> None:
        """Persist configuration back to YAML on disk."""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        text = yaml.safe_dump(self.config, sort_keys=True)
        self.config_path.write_text(text, encoding="utf-8")

    def _reload_if_changed(self) -> None:
        """Reload configuration from disk.

        Called before reads/writes so that external edits (e.g. by humans or
        other processes) are picked up without restart.
        """
        self.config = self._load()

    # --------------------------------------------------------------------- #
    # Public API
    # --------------------------------------------------------------------- #

    def is_enabled(self, flag: str, node: str | None = None) -> bool:
        """Check if a feature flag is enabled.

        Resolution order:
        1. Node-specific override in ``node_overrides[<node>][<flag>]``
        2. Global value in ``features[<flag>]``
        3. Default of ``False`` if unset
        """
        self._reload_if_changed()

        config = self.config
        if node:
            node_flags = config.get("node_overrides", {}).get(node, {})
            if isinstance(node_flags, dict) and flag in node_flags:
                return bool(node_flags[flag])

        return bool(config.get("features", {}).get(flag, False))

    def enable(self, flag: str, node: str | None = None) -> None:
        """Enable a feature flag globally or for a specific node."""
        self._reload_if_changed()

        if node:
            node_overrides = self.config.setdefault("node_overrides", {})
            node_flags = node_overrides.setdefault(node, {})
            if not isinstance(node_flags, dict):
                node_flags = {}
                node_overrides[node] = node_flags
            node_flags[flag] = True
        else:
            features = self.config.setdefault("features", {})
            if not isinstance(features, dict):
                features = {}
                self.config["features"] = features
            features[flag] = True

        self._save()

    def disable(self, flag: str, node: str | None = None) -> None:
        """Disable a feature flag globally or for a specific node."""
        self._reload_if_changed()

        if node:
            node_overrides = self.config.setdefault("node_overrides", {})
            node_flags = node_overrides.setdefault(node, {})
            if not isinstance(node_flags, dict):
                node_flags = {}
                node_overrides[node] = node_flags
            node_flags[flag] = False
        else:
            features = self.config.setdefault("features", {})
            if not isinstance(features, dict):
                features = {}
                self.config["features"] = features
            features[flag] = False

        self._save()

