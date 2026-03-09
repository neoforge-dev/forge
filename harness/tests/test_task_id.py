"""Tests for JIRA-style task ID generation."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from forge_harness.models.task_id import (
    DEFAULT_PREFIX,
    DOMAIN_PREFIXES,
    _counters_path,
    _load_counters,
    _save_counters,
    generate_task_id,
    resolve_prefix,
)


class TestResolvePrefix:
    """Tests for domain → prefix resolution."""

    def test_known_domain(self) -> None:
        assert resolve_prefix("interview-simulator") == "IS"

    def test_voice_coach(self) -> None:
        assert resolve_prefix("voice-coach") == "VC"

    def test_code_atlas(self) -> None:
        assert resolve_prefix("code-atlas") == "CA"

    def test_harness(self) -> None:
        assert resolve_prefix("harness") == "HRN"

    def test_unknown_domain_falls_back(self) -> None:
        assert resolve_prefix("unknown-domain") == DEFAULT_PREFIX

    def test_none_domain(self) -> None:
        assert resolve_prefix(None) == DEFAULT_PREFIX

    def test_empty_string_domain(self) -> None:
        assert resolve_prefix("") == DEFAULT_PREFIX

    def test_all_prefixes_are_uppercase(self) -> None:
        for prefix in DOMAIN_PREFIXES.values():
            assert prefix == prefix.upper()


class TestCountersPersistence:
    """Tests for counter file load/save."""

    def test_load_returns_empty_when_missing(self, tmp_path: Path) -> None:
        with patch(
            "forge_harness.models.task_id._counters_path", return_value=tmp_path / "nope.json"
        ):
            assert _load_counters() == {}

    def test_save_creates_file(self, tmp_path: Path) -> None:
        path = tmp_path / "_counters.json"
        with patch("forge_harness.models.task_id._counters_path", return_value=path):
            _save_counters({"IS": 5})
        assert path.exists()
        assert json.loads(path.read_text()) == {"IS": 5}

    def test_load_reads_saved(self, tmp_path: Path) -> None:
        path = tmp_path / "_counters.json"
        path.write_text('{"VC": 3}')
        with patch("forge_harness.models.task_id._counters_path", return_value=path):
            assert _load_counters() == {"VC": 3}

    def test_load_handles_corrupt_json(self, tmp_path: Path) -> None:
        path = tmp_path / "_counters.json"
        path.write_text("not json{{{")
        with patch("forge_harness.models.task_id._counters_path", return_value=path):
            assert _load_counters() == {}

    def test_save_creates_parent_dirs(self, tmp_path: Path) -> None:
        path = tmp_path / "deep" / "nested" / "_counters.json"
        with patch("forge_harness.models.task_id._counters_path", return_value=path):
            _save_counters({"CA": 1})
        assert path.exists()


class TestGenerateTaskId:
    """Tests for the main ID generation function."""

    def test_returns_prefixed_id(self, tmp_path: Path) -> None:
        path = tmp_path / "_counters.json"
        with patch("forge_harness.models.task_id._counters_path", return_value=path):
            result = generate_task_id("interview-simulator")
        assert result == "IS-001"

    def test_increments_counter(self, tmp_path: Path) -> None:
        path = tmp_path / "_counters.json"
        with patch("forge_harness.models.task_id._counters_path", return_value=path):
            first = generate_task_id("interview-simulator")
            second = generate_task_id("interview-simulator")
        assert first == "IS-001"
        assert second == "IS-002"

    def test_different_domains_independent_counters(self, tmp_path: Path) -> None:
        path = tmp_path / "_counters.json"
        with patch("forge_harness.models.task_id._counters_path", return_value=path):
            is_id = generate_task_id("interview-simulator")
            vc_id = generate_task_id("voice-coach")
        assert is_id == "IS-001"
        assert vc_id == "VC-001"

    def test_default_prefix_when_no_domain(self, tmp_path: Path) -> None:
        path = tmp_path / "_counters.json"
        with patch("forge_harness.models.task_id._counters_path", return_value=path):
            result = generate_task_id()
        assert result == "TASK-001"

    def test_default_prefix_when_none(self, tmp_path: Path) -> None:
        path = tmp_path / "_counters.json"
        with patch("forge_harness.models.task_id._counters_path", return_value=path):
            result = generate_task_id(None)
        assert result == "TASK-001"

    def test_zero_padding(self, tmp_path: Path) -> None:
        path = tmp_path / "_counters.json"
        with patch("forge_harness.models.task_id._counters_path", return_value=path):
            for _ in range(9):
                generate_task_id("harness")
            tenth = generate_task_id("harness")
        assert tenth == "HRN-010"

    def test_grows_past_999(self, tmp_path: Path) -> None:
        path = tmp_path / "_counters.json"
        path.write_text('{"IS": 999}')
        with patch("forge_harness.models.task_id._counters_path", return_value=path):
            result = generate_task_id("interview-simulator")
        assert result == "IS-1000"

    def test_counter_persists_across_calls(self, tmp_path: Path) -> None:
        path = tmp_path / "_counters.json"
        with patch("forge_harness.models.task_id._counters_path", return_value=path):
            generate_task_id("forge")
            generate_task_id("forge")
        counters = json.loads(path.read_text())
        assert counters["FRG"] == 2

    def test_unknown_domain_uses_task_prefix(self, tmp_path: Path) -> None:
        path = tmp_path / "_counters.json"
        with patch("forge_harness.models.task_id._counters_path", return_value=path):
            result = generate_task_id("completely-unknown")
        assert result.startswith("TASK-")

    def test_thread_safety(self, tmp_path: Path) -> None:
        """Concurrent calls produce unique IDs."""
        path = tmp_path / "_counters.json"
        results: list[str] = []
        lock = threading.Lock()

        def gen() -> None:
            tid = generate_task_id("harness")
            with lock:
                results.append(tid)

        with patch("forge_harness.models.task_id._counters_path", return_value=path):
            threads = [threading.Thread(target=gen) for _ in range(20)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        assert len(results) == 20
        assert len(set(results)) == 20  # All unique
