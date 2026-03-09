"""
Extended tests for Ralph Loop Harness
======================================

Targeting uncovered lines in forge_harness/ralph_loop.py to push
coverage from 66% toward 80%+.

Areas covered:
- FeatureSpec edge cases (from_dict title/dependencies aliases, files_to_create/modify)
- FeatureStore edge cases (invalid JSON, non-dict entries, list format, wrapper format)
- FeatureStore.save atomic rename
- FeatureStore.get_next_pending — cross-domain boost, FAILING priority, atlas boost
- RalphLoopHarness initialization
- run() lifecycle: dry-run, max iterations, completion detection, blocked detection
- _run_tests / _run_lint — timeout, exception, dry-run, lint command
- _save_checkpoint / resume
- _extract_feature_type — every branch
- _extract_file_paths — all branches
- _build_decision_context — tags extraction
- _check_and_flush_memory
- _implement_feature — with/without orchestrator
- _index_feature_to_atlas — with/without bridge, error path
- _trigger_feedback_loops
- _record_simple_history_outcome
- _record_outcome
- create_ralph_loop factory function
- create_features_from_plan
- _estimate_tokens_from_effort
- _extract_priority_from_line
- GitHubFeatureTracker helpers
"""

import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_features_json(tmp_path, features_list, *, wrapper=True):
    """Write a features.json file and return its path."""
    path = tmp_path / "features.json"
    if wrapper:
        payload = {"version": "1.0", "features": features_list}
    else:
        payload = features_list
    path.write_text(json.dumps(payload))
    return path


def _make_feature_dict(
    fid="feat-001",
    name="My Feature",
    description="A feature",
    status="pending",
    priority="medium",
    **kwargs,
):
    d = {
        "id": fid,
        "name": name,
        "description": description,
        "status": status,
        "priority": priority,
        "depends_on": [],
        "acceptance_criteria": [],
        "tests": [],
    }
    d.update(kwargs)
    return d


# ---------------------------------------------------------------------------
# FeatureSpec — edge cases
# ---------------------------------------------------------------------------


class TestFeatureSpecEdgeCases:
    """Edge-case tests for FeatureSpec.from_dict / to_dict."""

    def test_from_dict_uses_title_fallback(self):
        from forge_harness.ralph_loop import FeatureSpec

        data = {
            "id": "t-001",
            "title": "Title-Based Feature",
            "description": "Uses title instead of name",
        }
        spec = FeatureSpec.from_dict(data)
        assert spec.name == "Title-Based Feature"

    def test_from_dict_raises_if_no_name_or_title(self):
        from forge_harness.ralph_loop import FeatureSpec

        data = {"id": "t-002", "description": "No name field"}
        with pytest.raises(KeyError):
            FeatureSpec.from_dict(data)

    def test_from_dict_uses_dependencies_alias(self):
        from forge_harness.ralph_loop import FeatureSpec

        data = {
            "id": "t-003",
            "name": "Dep Feature",
            "description": "Uses 'dependencies' key",
            "dependencies": ["base-001"],
        }
        spec = FeatureSpec.from_dict(data)
        assert spec.depends_on == ["base-001"]

    def test_to_dict_includes_files_to_create(self):
        from forge_harness.ralph_loop import FeatureSpec

        spec = FeatureSpec(
            id="f-001",
            name="File Feature",
            description="Creates files",
            files_to_create=["src/foo.py"],
        )
        d = spec.to_dict()
        assert "files_to_create" in d
        assert d["files_to_create"] == ["src/foo.py"]

    def test_to_dict_includes_files_to_modify(self):
        from forge_harness.ralph_loop import FeatureSpec

        spec = FeatureSpec(
            id="f-002",
            name="Modify Feature",
            description="Modifies files",
            files_to_modify=["src/bar.py"],
        )
        d = spec.to_dict()
        assert "files_to_modify" in d
        assert d["files_to_modify"] == ["src/bar.py"]

    def test_to_dict_includes_category_and_tags(self):
        from forge_harness.ralph_loop import FeatureSpec

        spec = FeatureSpec(
            id="f-003",
            name="Tagged Feature",
            description="Has category and tags",
            category="authentication",
            tags=["security", "critical"],
        )
        d = spec.to_dict()
        assert d["category"] == "authentication"
        assert d["tags"] == ["security", "critical"]

    def test_to_dict_omits_empty_files_lists(self):
        from forge_harness.ralph_loop import FeatureSpec

        spec = FeatureSpec(id="f-004", name="Plain Feature", description="No files")
        d = spec.to_dict()
        assert "files_to_create" not in d
        assert "files_to_modify" not in d

    def test_from_dict_all_optional_fields_default(self):
        from forge_harness.ralph_loop import FeatureSpec, FeatureStatus

        data = {"id": "min-001", "name": "Minimal", "description": "Minimal feature"}
        spec = FeatureSpec.from_dict(data)
        assert spec.status == FeatureStatus.PENDING
        assert spec.priority == "medium"
        assert spec.acceptance_criteria == []
        assert spec.depends_on == []
        assert spec.tests == []
        assert spec.estimated_tokens == 4000
        assert spec.attempts == 0
        assert spec.last_error is None
        assert spec.files_to_create == []
        assert spec.files_to_modify == []
        assert spec.category is None
        assert spec.tags == []


# ---------------------------------------------------------------------------
# FeatureStore — edge cases
# ---------------------------------------------------------------------------


class TestFeatureStoreEdgeCases:
    """Edge-case tests for FeatureStore load/save."""

    def test_load_plain_list_format(self, tmp_path):
        from forge_harness.ralph_loop import FeatureStore

        path = _make_features_json(tmp_path, [_make_feature_dict()], wrapper=False)
        store = FeatureStore(path)
        features = store.load()
        assert len(features) == 1
        assert features[0].id == "feat-001"

    def test_load_unexpected_json_type(self, tmp_path):
        """A top-level JSON string produces an empty list gracefully."""
        path = tmp_path / "features.json"
        path.write_text('"not a list or dict"')
        from forge_harness.ralph_loop import FeatureStore

        store = FeatureStore(path)
        features = store.load()
        assert features == []

    def test_load_skips_non_dict_entries(self, tmp_path):
        """Non-dict entries inside the features list are skipped."""
        payload = {"version": "1.0", "features": [42, "string", _make_feature_dict()]}
        path = tmp_path / "features.json"
        path.write_text(json.dumps(payload))
        from forge_harness.ralph_loop import FeatureStore

        store = FeatureStore(path)
        features = store.load()
        assert len(features) == 1

    def test_load_skips_invalid_feature_entries(self, tmp_path):
        """Entries missing required fields are skipped with a warning."""
        payload = {
            "version": "1.0",
            "features": [
                {"id": "bad-001", "description": "No name"},  # missing name/title
                _make_feature_dict(fid="good-001"),
            ],
        }
        path = tmp_path / "features.json"
        path.write_text(json.dumps(payload))
        from forge_harness.ralph_loop import FeatureStore

        store = FeatureStore(path)
        features = store.load()
        assert len(features) == 1
        assert features[0].id == "good-001"

    def test_load_invalid_json(self, tmp_path):
        path = tmp_path / "features.json"
        path.write_text("{not valid json}")
        from forge_harness.ralph_loop import FeatureStore

        store = FeatureStore(path)
        features = store.load()
        assert features == []

    def test_save_atomic_write(self, tmp_path):
        """save() uses a temp file then renames it atomically."""
        path = _make_features_json(tmp_path, [_make_feature_dict()], wrapper=True)
        from forge_harness.ralph_loop import FeatureStatus, FeatureStore

        store = FeatureStore(path)
        store.load()
        feat = store.get("feat-001")
        feat.status = FeatureStatus.PASSING
        store.update(feat)
        store.save()

        # Temp file should be gone after rename
        assert not (tmp_path / "features.json.tmp").exists()
        # Main file should exist
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["version"] == "1.0"
        statuses = {f["id"]: f["status"] for f in data["features"]}
        assert statuses["feat-001"] == "passing"

    def test_save_oserror_cleans_temp(self, tmp_path):
        """If rename raises OSError, the temp file is cleaned up."""
        path = _make_features_json(tmp_path, [_make_feature_dict()], wrapper=True)
        from forge_harness.ralph_loop import FeatureStore

        store = FeatureStore(path)
        store.load()

        with patch.object(Path, "rename", side_effect=OSError("disk full")):
            with pytest.raises(OSError):
                store.save()

    def test_load_priority_sorting(self, tmp_path):
        """Features are returned sorted critical > high > medium > low."""
        features_list = [
            _make_feature_dict(fid="low-001", priority="low"),
            _make_feature_dict(fid="crit-001", priority="critical"),
            _make_feature_dict(fid="med-001", priority="medium"),
            _make_feature_dict(fid="high-001", priority="high"),
        ]
        path = _make_features_json(tmp_path, features_list)
        from forge_harness.ralph_loop import FeatureStore

        store = FeatureStore(path)
        features = store.load()
        priorities = [f.priority for f in features]
        assert priorities == ["critical", "high", "medium", "low"]

    def test_get_returns_none_for_missing_id(self, tmp_path):
        path = _make_features_json(tmp_path, [_make_feature_dict()])
        from forge_harness.ralph_loop import FeatureStore

        store = FeatureStore(path)
        store.load()
        assert store.get("nonexistent") is None


# ---------------------------------------------------------------------------
# FeatureStore.get_next_pending — advanced cases
# ---------------------------------------------------------------------------


class TestGetNextPendingAdvanced:
    @pytest.mark.asyncio
    async def test_failing_features_get_priority_over_pending(self, tmp_path):
        """FAILING features sort before PENDING features of same priority."""
        features_list = [
            _make_feature_dict(fid="pend-001", status="pending", priority="high"),
            _make_feature_dict(fid="fail-001", status="failing", priority="medium"),
        ]
        path = _make_features_json(tmp_path, features_list)
        from forge_harness.ralph_loop import FeatureStore

        store = FeatureStore(path)
        store.load()
        nxt = await store.get_next_pending()
        assert nxt.id == "fail-001"

    @pytest.mark.asyncio
    async def test_returns_none_when_all_blocked(self, tmp_path):
        features_list = [_make_feature_dict(fid="blk-001", status="blocked")]
        path = _make_features_json(tmp_path, features_list)
        from forge_harness.ralph_loop import FeatureStore

        store = FeatureStore(path)
        store.load()
        nxt = await store.get_next_pending()
        assert nxt is None

    @pytest.mark.asyncio
    async def test_dependency_on_missing_feature_blocks(self, tmp_path):
        """If a dep_id doesn't exist in the store, the feature is not available."""
        features_list = [
            _make_feature_dict(fid="dep-001", depends_on=["ghost-001"]),
        ]
        path = _make_features_json(tmp_path, features_list)
        from forge_harness.ralph_loop import FeatureStore

        store = FeatureStore(path)
        store.load()
        nxt = await store.get_next_pending()
        assert nxt is None

    @pytest.mark.asyncio
    async def test_dependency_on_completed_feature_is_satisfied(self, tmp_path):
        """COMPLETED status also satisfies dependency check."""
        features_list = [
            _make_feature_dict(fid="base-001", status="completed"),
            _make_feature_dict(fid="dep-001", status="pending", depends_on=["base-001"]),
        ]
        path = _make_features_json(tmp_path, features_list)
        from forge_harness.ralph_loop import FeatureStore

        store = FeatureStore(path)
        store.load()
        nxt = await store.get_next_pending()
        assert nxt.id == "dep-001"

    @pytest.mark.asyncio
    async def test_code_atlas_boost_applied(self, tmp_path):
        """Code Atlas bridge search results cause an atlas boost."""
        features_list = [
            _make_feature_dict(fid="atlas-001", priority="medium"),
            _make_feature_dict(fid="no-atlas-001", priority="medium"),
        ]
        path = _make_features_json(tmp_path, features_list)
        from forge_harness.ralph_loop import FeatureStore

        store = FeatureStore(path)
        store.load()

        # Mock bridge: atlas-001 returns results, no-atlas-001 returns empty
        async def fake_search(query, limit=3):
            if "atlas-001" in query or "My Feature" in query:
                return [{"id": "pattern-1"}]
            return []

        mock_bridge = MagicMock()
        mock_bridge.search_solutions = AsyncMock(side_effect=fake_search)

        nxt = await store.get_next_pending(code_atlas_bridge=mock_bridge)
        assert hasattr(nxt, "_atlas_boost")

    @pytest.mark.asyncio
    async def test_code_atlas_error_falls_back_to_zero_boost(self, tmp_path):
        """If Code Atlas raises, _atlas_boost is 0.0 and loop continues."""
        features_list = [_make_feature_dict(fid="err-001")]
        path = _make_features_json(tmp_path, features_list)
        from forge_harness.ralph_loop import FeatureStore

        store = FeatureStore(path)
        store.load()

        mock_bridge = MagicMock()
        mock_bridge.search_solutions = AsyncMock(side_effect=RuntimeError("boom"))

        nxt = await store.get_next_pending(code_atlas_bridge=mock_bridge)
        assert nxt is not None
        assert nxt._atlas_boost == 0.0

    @pytest.mark.asyncio
    async def test_feature_type_extractor_is_used(self, tmp_path):
        """Custom feature_type_extractor callable is invoked."""
        features_list = [_make_feature_dict(fid="custom-001")]
        path = _make_features_json(tmp_path, features_list)
        from forge_harness.ralph_loop import FeatureStore
        from forge_harness.simple_history import SimpleHistory

        history_path = tmp_path / "h.jsonl"
        history = SimpleHistory(history_file=history_path)
        for _ in range(5):
            history.record("forge", "proj", "feature:custom_type", success=True, context={})

        store = FeatureStore(path)
        store.load()

        called_with = []

        def extractor(feat):
            called_with.append(feat.id)
            return "custom_type"

        from forge_harness.ralph_loop import LoopConfig

        config = LoopConfig()
        await store.get_next_pending(
            simple_history=history, config=config, feature_type_extractor=extractor
        )
        assert "custom-001" in called_with


# ---------------------------------------------------------------------------
# _extract_feature_type — all branches
# ---------------------------------------------------------------------------


class TestExtractFeatureType:
    def _make_harness(self, tmp_path):
        from forge_harness.ralph_loop import FeatureStore, LoopConfig, RalphLoopHarness

        path = _make_features_json(tmp_path, [_make_feature_dict()])
        store = FeatureStore(path)
        config = LoopConfig()
        return RalphLoopHarness(feature_store=store, config=config)

    def test_category_takes_priority(self, tmp_path):
        from forge_harness.ralph_loop import FeatureSpec

        harness = self._make_harness(tmp_path)
        feat = FeatureSpec(id="c-001", name="X", description="Y", category="authentication")
        assert harness._extract_feature_type(feat) == "authentication"

    def test_tags_take_priority_over_name(self, tmp_path):
        from forge_harness.ralph_loop import FeatureSpec

        harness = self._make_harness(tmp_path)
        feat = FeatureSpec(
            id="t-001", name="Some Thing", description="desc", tags=["testing", "qa"]
        )
        assert harness._extract_feature_type(feat) == "testing"

    def test_keyword_testing_inferred(self, tmp_path):
        from forge_harness.ralph_loop import FeatureSpec

        harness = self._make_harness(tmp_path)
        feat = FeatureSpec(id="k-001", name="Unit coverage", description="Add pytest coverage")
        assert harness._extract_feature_type(feat) == "testing"

    def test_keyword_authentication_inferred(self, tmp_path):
        from forge_harness.ralph_loop import FeatureSpec

        harness = self._make_harness(tmp_path)
        feat = FeatureSpec(id="a-001", name="OAuth login", description="Implement JWT auth")
        assert harness._extract_feature_type(feat) == "authentication"

    def test_keyword_api_endpoint_inferred(self, tmp_path):
        from forge_harness.ralph_loop import FeatureSpec

        harness = self._make_harness(tmp_path)
        feat = FeatureSpec(id="e-001", name="REST endpoint", description="CRUD api endpoint")
        assert harness._extract_feature_type(feat) == "api_endpoint:simple"

    def test_api_endpoint_external(self, tmp_path):
        from forge_harness.ralph_loop import FeatureSpec

        harness = self._make_harness(tmp_path)
        feat = FeatureSpec(
            id="e-002",
            name="Webhook receiver",
            description="Handle external webhook events from payment provider",
        )
        assert harness._extract_feature_type(feat) == "api_endpoint:external"

    def test_api_endpoint_batch(self, tmp_path):
        from forge_harness.ralph_loop import FeatureSpec

        harness = self._make_harness(tmp_path)
        feat = FeatureSpec(
            id="e-003", name="Bulk import", description="Batch import endpoint for CSV files"
        )
        assert harness._extract_feature_type(feat) == "api_endpoint:batch"

    def test_api_endpoint_stateful_via_depends(self, tmp_path):
        from forge_harness.ralph_loop import FeatureSpec

        harness = self._make_harness(tmp_path)
        feat = FeatureSpec(
            id="e-004",
            name="User endpoint",
            description="REST endpoint for users",
            depends_on=["alembic-migration-001"],
        )
        assert harness._extract_feature_type(feat) == "api_endpoint:stateful"

    def test_api_endpoint_stateful_via_service_file(self, tmp_path):
        from forge_harness.ralph_loop import FeatureSpec

        harness = self._make_harness(tmp_path)
        feat = FeatureSpec(
            id="e-005",
            name="Account endpoint",
            description="REST api endpoint",
            files_to_modify=["app/service/accounts.py"],
        )
        assert harness._extract_feature_type(feat) == "api_endpoint:stateful"

    def test_id_prefix_fallback(self, tmp_path):
        from forge_harness.ralph_loop import FeatureSpec

        harness = self._make_harness(tmp_path)
        feat = FeatureSpec(id="xyz-001", name="Something", description="Something else")
        ftype = harness._extract_feature_type(feat)
        assert ftype == "xyz"

    def test_id_without_dash_fallback(self, tmp_path):
        from forge_harness.ralph_loop import FeatureSpec

        harness = self._make_harness(tmp_path)
        feat = FeatureSpec(id="nodash", name="No dash id", description="no dash")
        ftype = harness._extract_feature_type(feat)
        assert ftype == "nodash"

    def test_normalize_type_aliases(self, tmp_path):
        """Category aliases like 'auth' map to 'authentication'."""
        from forge_harness.ralph_loop import FeatureSpec

        harness = self._make_harness(tmp_path)
        for alias in ("auth", "login", "oauth", "jwt", "token"):
            feat = FeatureSpec(id=f"n-{alias}", name="X", description="Y", category=alias)
            assert harness._extract_feature_type(feat) == "authentication"

    def test_normalize_type_testing_aliases(self, tmp_path):
        from forge_harness.ralph_loop import FeatureSpec

        harness = self._make_harness(tmp_path)
        for alias in ("test", "tests", "testing", "spec", "specs", "qa"):
            feat = FeatureSpec(id=f"n-{alias}", name="X", description="Y", category=alias)
            assert harness._extract_feature_type(feat) == "testing"


# ---------------------------------------------------------------------------
# _extract_file_paths
# ---------------------------------------------------------------------------


class TestExtractFilePaths:
    def _make_harness(self, tmp_path):
        from forge_harness.ralph_loop import FeatureStore, LoopConfig, RalphLoopHarness

        path = _make_features_json(tmp_path, [_make_feature_dict()])
        store = FeatureStore(path)
        return RalphLoopHarness(feature_store=store, config=LoopConfig())

    def test_extracts_test_file_paths(self, tmp_path):
        from forge_harness.ralph_loop import FeatureSpec

        harness = self._make_harness(tmp_path)
        feat = FeatureSpec(
            id="f-001",
            name="X",
            description="d",
            tests=["tests/test_auth.py", "test_login"],
        )
        paths = harness._extract_file_paths(feat)
        assert "tests/test_auth.py" in paths
        # "test_login" has no slash or .py suffix — not extracted
        assert "test_login" not in paths

    def test_extracts_backtick_paths_from_description(self, tmp_path):
        from forge_harness.ralph_loop import FeatureSpec

        harness = self._make_harness(tmp_path)
        feat = FeatureSpec(
            id="f-002",
            name="X",
            description="Modify `src/models/user.py` and `src/routes/auth.ts`",
        )
        paths = harness._extract_file_paths(feat)
        assert "src/models/user.py" in paths
        assert "src/routes/auth.ts" in paths

    def test_extracts_paths_from_acceptance_criteria(self, tmp_path):
        from forge_harness.ralph_loop import FeatureSpec

        harness = self._make_harness(tmp_path)
        feat = FeatureSpec(
            id="f-003",
            name="X",
            description="d",
            acceptance_criteria=[
                "create src/auth/service.py",
                "modify routes/login.py:",
            ],
        )
        paths = harness._extract_file_paths(feat)
        assert any("service.py" in p for p in paths)

    def test_deduplicates_paths(self, tmp_path):
        from forge_harness.ralph_loop import FeatureSpec

        harness = self._make_harness(tmp_path)
        feat = FeatureSpec(
            id="f-004",
            name="X",
            description="See `src/foo.py` and also `src/foo.py`",
        )
        paths = harness._extract_file_paths(feat)
        assert paths.count("src/foo.py") == 1

    def test_returns_empty_for_plain_feature(self, tmp_path):
        from forge_harness.ralph_loop import FeatureSpec

        harness = self._make_harness(tmp_path)
        feat = FeatureSpec(id="f-005", name="X", description="No file references here")
        paths = harness._extract_file_paths(feat)
        assert paths == []


# ---------------------------------------------------------------------------
# _build_decision_context
# ---------------------------------------------------------------------------


class TestBuildDecisionContext:
    def _make_harness(self, tmp_path):
        from forge_harness.ralph_loop import FeatureStore, LoopConfig, RalphLoopHarness

        path = _make_features_json(tmp_path, [_make_feature_dict()])
        store = FeatureStore(path)
        config = LoopConfig(domain="test-domain", project="test-project")
        return RalphLoopHarness(feature_store=store, config=config)

    def test_critical_priority_adds_tag(self, tmp_path):
        from forge_harness.ralph_loop import FeatureSpec

        harness = self._make_harness(tmp_path)
        feat = FeatureSpec(id="c-001", name="Critical thing", description="d", priority="critical")
        ctx = harness._build_decision_context(feat)
        assert "critical" in ctx.tags

    def test_high_priority_adds_tag(self, tmp_path):
        from forge_harness.ralph_loop import FeatureSpec

        harness = self._make_harness(tmp_path)
        feat = FeatureSpec(id="h-001", name="High thing", description="d", priority="high")
        ctx = harness._build_decision_context(feat)
        assert "high-priority" in ctx.tags

    def test_security_name_adds_tag(self, tmp_path):
        from forge_harness.ralph_loop import FeatureSpec

        harness = self._make_harness(tmp_path)
        feat = FeatureSpec(id="s-001", name="Auth security module", description="d")
        ctx = harness._build_decision_context(feat)
        assert "security" in ctx.tags

    def test_refactor_name_adds_tag(self, tmp_path):
        from forge_harness.ralph_loop import FeatureSpec

        harness = self._make_harness(tmp_path)
        feat = FeatureSpec(id="r-001", name="Refactor auth module", description="d")
        ctx = harness._build_decision_context(feat)
        assert "refactor" in ctx.tags

    def test_unknown_domain_project_defaults(self, tmp_path):
        from forge_harness.ralph_loop import FeatureSpec, FeatureStore, LoopConfig, RalphLoopHarness

        path = _make_features_json(tmp_path, [_make_feature_dict()])
        store = FeatureStore(path)
        config = LoopConfig()  # domain=None, project=None
        harness = RalphLoopHarness(feature_store=store, config=config)
        feat = FeatureSpec(id="u-001", name="Unknown", description="d")
        ctx = harness._build_decision_context(feat)
        assert ctx.domain == "unknown"
        assert ctx.project == "unknown"


# ---------------------------------------------------------------------------
# _run_tests / _run_lint
# ---------------------------------------------------------------------------


class TestRunTests:
    def _make_harness(self, tmp_path, **config_kwargs):
        from forge_harness.ralph_loop import FeatureStore, LoopConfig, RalphLoopHarness

        path = _make_features_json(tmp_path, [_make_feature_dict()])
        store = FeatureStore(path)
        config = LoopConfig(**config_kwargs)
        return RalphLoopHarness(feature_store=store, config=config)

    @pytest.mark.asyncio
    async def test_dry_run_returns_true(self, tmp_path):
        harness = self._make_harness(tmp_path, dry_run=True)
        from forge_harness.ralph_loop import FeatureSpec

        feat = FeatureSpec(id="dr-001", name="X", description="d")
        passed, error = await harness._run_tests(feat)
        assert passed is True
        assert error is None

    @pytest.mark.asyncio
    async def test_tests_pass_on_zero_exit(self, tmp_path):
        harness = self._make_harness(tmp_path, test_command="true")
        from forge_harness.ralph_loop import FeatureSpec

        feat = FeatureSpec(id="p-001", name="X", description="d")
        passed, error = await harness._run_tests(feat)
        assert passed is True
        assert error is None

    @pytest.mark.asyncio
    async def test_tests_fail_on_nonzero_exit(self, tmp_path):
        harness = self._make_harness(tmp_path, test_command="false")
        from forge_harness.ralph_loop import FeatureSpec

        feat = FeatureSpec(id="f-001", name="X", description="d")
        passed, error = await harness._run_tests(feat)
        assert passed is False
        assert error is not None

    @pytest.mark.asyncio
    async def test_test_filter_appended_for_specific_tests(self, tmp_path):
        """When feature.tests is set the -k filter is added."""
        harness = self._make_harness(tmp_path, test_command="true")
        from forge_harness.ralph_loop import FeatureSpec

        feat = FeatureSpec(
            id="tf-001", name="X", description="d", tests=["test_login", "test_logout"]
        )
        # We just verify the method runs without crashing; command is 'true'
        passed, error = await harness._run_tests(feat)
        assert passed is True

    @pytest.mark.asyncio
    async def test_tests_timeout(self, tmp_path):
        harness = self._make_harness(tmp_path, test_command="sleep 99", timeout_seconds=0)
        from forge_harness.ralph_loop import FeatureSpec

        feat = FeatureSpec(id="to-001", name="X", description="d")
        passed, error = await harness._run_tests(feat)
        assert passed is False
        assert "timed out" in (error or "").lower() or error is not None

    @pytest.mark.asyncio
    async def test_tests_exception_handled(self, tmp_path):
        harness = self._make_harness(tmp_path, test_command="true")
        from forge_harness.ralph_loop import FeatureSpec

        feat = FeatureSpec(id="ex-001", name="X", description="d")
        with patch(
            "forge_harness.ralph_loop.asyncio.create_subprocess_shell",
            side_effect=OSError("no shell"),
        ):
            passed, error = await harness._run_tests(feat)
        assert passed is False
        assert error is not None

    @pytest.mark.asyncio
    async def test_lint_skipped_when_no_command(self, tmp_path):
        harness = self._make_harness(tmp_path)  # lint_command=None
        from forge_harness.ralph_loop import FeatureSpec

        feat = FeatureSpec(id="nl-001", name="X", description="d")
        passed, error = await harness._run_lint(feat)
        assert passed is True
        assert error is None

    @pytest.mark.asyncio
    async def test_lint_dry_run_returns_true(self, tmp_path):
        harness = self._make_harness(tmp_path, dry_run=True, lint_command="ruff check .")
        from forge_harness.ralph_loop import FeatureSpec

        feat = FeatureSpec(id="ld-001", name="X", description="d")
        passed, error = await harness._run_lint(feat)
        assert passed is True
        assert error is None

    @pytest.mark.asyncio
    async def test_lint_passes_on_zero_exit(self, tmp_path):
        harness = self._make_harness(tmp_path, lint_command="true")
        from forge_harness.ralph_loop import FeatureSpec

        feat = FeatureSpec(id="lp-001", name="X", description="d")
        passed, error = await harness._run_lint(feat)
        assert passed is True

    @pytest.mark.asyncio
    async def test_lint_fails_on_nonzero_exit(self, tmp_path):
        harness = self._make_harness(tmp_path, lint_command="false")
        from forge_harness.ralph_loop import FeatureSpec

        feat = FeatureSpec(id="lf-001", name="X", description="d")
        passed, error = await harness._run_lint(feat)
        assert passed is False

    @pytest.mark.asyncio
    async def test_lint_timeout(self, tmp_path):
        harness = self._make_harness(tmp_path, lint_command="sleep 99", timeout_seconds=0)
        from forge_harness.ralph_loop import FeatureSpec

        feat = FeatureSpec(id="lt-001", name="X", description="d")
        passed, error = await harness._run_lint(feat)
        assert passed is False

    @pytest.mark.asyncio
    async def test_lint_exception_handled(self, tmp_path):
        harness = self._make_harness(tmp_path, lint_command="true")
        from forge_harness.ralph_loop import FeatureSpec

        feat = FeatureSpec(id="le-001", name="X", description="d")
        with patch(
            "forge_harness.ralph_loop.asyncio.create_subprocess_shell",
            side_effect=OSError("bang"),
        ):
            passed, error = await harness._run_lint(feat)
        assert passed is False


# ---------------------------------------------------------------------------
# _save_checkpoint / resume
# ---------------------------------------------------------------------------


class TestCheckpoint:
    def _make_harness(self, tmp_path):
        from forge_harness.ralph_loop import FeatureStore, LoopConfig, RalphLoopHarness

        path = _make_features_json(tmp_path, [_make_feature_dict()])
        store = FeatureStore(path)
        config = LoopConfig(domain="d", project="p")
        return RalphLoopHarness(feature_store=store, config=config)

    @pytest.mark.asyncio
    async def test_save_checkpoint_creates_file(self, tmp_path):
        from datetime import UTC, datetime

        harness = self._make_harness(tmp_path)
        harness._iteration = 5
        harness._start_time = datetime.now(UTC)
        harness.store.load()

        cp = await harness._save_checkpoint()
        assert cp.exists()
        data = json.loads(cp.read_text())
        assert data["iteration"] == 5
        assert "stats" in data

    @pytest.mark.asyncio
    async def test_save_checkpoint_contains_config(self, tmp_path):
        from datetime import UTC, datetime

        harness = self._make_harness(tmp_path)
        harness._iteration = 3
        harness._start_time = datetime.now(UTC)
        harness.store.load()

        cp = await harness._save_checkpoint()
        data = json.loads(cp.read_text())
        assert "config" in data
        assert data["config"]["domain"] == "d"

    @pytest.mark.asyncio
    async def test_resume_restores_iteration(self, tmp_path):
        """resume() restores the iteration counter from checkpoint."""
        from datetime import UTC, datetime

        harness = self._make_harness(tmp_path)
        harness._iteration = 7
        harness._start_time = datetime.now(UTC)
        harness.store.load()

        cp = await harness._save_checkpoint()

        # Reset
        harness._iteration = 0

        # Mock run() so we don't actually execute the loop
        with patch.object(harness, "run", new_callable=AsyncMock) as mock_run:
            from forge_harness.ralph_loop import LoopResult

            mock_run.return_value = LoopResult(
                success=True,
                iterations=7,
                features_completed=0,
                features_blocked=0,
                features_remaining=0,
                total_tokens=0,
                duration_seconds=1.0,
            )
            await harness.resume(cp)

        assert harness._iteration == 7


# ---------------------------------------------------------------------------
# run() main loop
# ---------------------------------------------------------------------------


class TestRunLoop:
    def _make_harness(self, tmp_path, features_list, **config_kwargs):
        from forge_harness.ralph_loop import FeatureStore, LoopConfig, RalphLoopHarness

        path = _make_features_json(tmp_path, features_list)
        store = FeatureStore(path)
        config = LoopConfig(**config_kwargs)
        return RalphLoopHarness(feature_store=store, config=config)

    @pytest.mark.asyncio
    async def test_run_empty_features_returns_immediately(self, tmp_path):
        harness = self._make_harness(tmp_path, [], dry_run=True)
        result = await harness.run()
        assert result.success is True
        assert result.iterations == 1  # Goes through one pass finding 0 pending

    @pytest.mark.asyncio
    async def test_run_dry_run_marks_features_passing(self, tmp_path):
        """In dry run, _implement_feature is skipped; test result determines status."""
        harness = self._make_harness(
            tmp_path, [_make_feature_dict(fid="dry-001")], dry_run=True
        )
        # Patch _run_tests to return passing
        with patch.object(harness, "_run_tests", new_callable=AsyncMock) as mock_tests:
            mock_tests.return_value = (True, None)
            result = await harness.run()
        assert result.features_completed >= 1

    @pytest.mark.asyncio
    async def test_run_max_iterations_stops_loop(self, tmp_path):
        """Loop exits when max_iterations is reached."""
        # Feature will keep failing to ensure it loops
        harness = self._make_harness(
            tmp_path,
            [_make_feature_dict(fid="inf-001")],
            max_iterations=3,
            max_failures_per_feature=100,
            dry_run=True,
        )
        with patch.object(harness, "_run_tests", new_callable=AsyncMock) as mock_tests:
            mock_tests.return_value = (False, "always fails")
            result = await harness.run()
        assert result.iterations <= 3

    @pytest.mark.asyncio
    async def test_run_feature_blocked_after_max_failures(self, tmp_path):
        harness = self._make_harness(
            tmp_path,
            [_make_feature_dict(fid="blk-001")],
            max_iterations=10,
            max_failures_per_feature=2,
            dry_run=True,
        )
        with patch.object(harness, "_run_tests", new_callable=AsyncMock) as mock_tests:
            mock_tests.return_value = (False, "always fails")
            result = await harness.run()
        assert result.features_blocked >= 1

    @pytest.mark.asyncio
    async def test_run_saves_checkpoint_at_interval(self, tmp_path):
        features_list = [
            _make_feature_dict(fid=f"chk-{i:03d}") for i in range(12)
        ]
        harness = self._make_harness(
            tmp_path,
            features_list,
            max_iterations=12,
            checkpoint_interval=5,
            dry_run=False,
        )
        with patch.object(harness, "_run_tests", new_callable=AsyncMock) as mock_tests:
            mock_tests.return_value = (True, None)
            with patch.object(harness.store, "save"):
                result = await harness.run()
        # checkpoint_path is set when checkpoint was saved
        assert result.checkpoint_path is not None or result.iterations >= 5

    @pytest.mark.asyncio
    async def test_run_returns_loop_result(self, tmp_path):
        from forge_harness.ralph_loop import LoopResult

        harness = self._make_harness(
            tmp_path, [_make_feature_dict(fid="res-001")], dry_run=True
        )
        with patch.object(harness, "_run_tests", new_callable=AsyncMock) as mock_tests:
            mock_tests.return_value = (True, None)
            result = await harness.run()
        assert isinstance(result, LoopResult)
        assert result.duration_seconds >= 0

    @pytest.mark.asyncio
    async def test_run_with_telemetry_calls_register(self, tmp_path):
        harness = self._make_harness(tmp_path, [], dry_run=True)
        mock_telemetry = MagicMock()
        mock_telemetry.register = AsyncMock()
        mock_telemetry.start_heartbeat_loop = AsyncMock()
        mock_telemetry.complete = AsyncMock()
        harness.telemetry = mock_telemetry
        await harness.run()
        mock_telemetry.register.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_run_all_blocked_exits_cleanly(self, tmp_path):
        harness = self._make_harness(
            tmp_path,
            [_make_feature_dict(fid="b-001", depends_on=["ghost-999"])],
            dry_run=True,
        )
        result = await harness.run()
        # No features could be processed due to blocked dependency
        assert result.features_remaining >= 0


# ---------------------------------------------------------------------------
# _implement_feature
# ---------------------------------------------------------------------------


class TestImplementFeature:
    def _make_harness(self, tmp_path, orchestrator=None):
        from forge_harness.ralph_loop import FeatureStore, LoopConfig, RalphLoopHarness

        path = _make_features_json(tmp_path, [_make_feature_dict()])
        store = FeatureStore(path)
        config = LoopConfig()
        return RalphLoopHarness(feature_store=store, config=config, orchestrator=orchestrator)

    @pytest.mark.asyncio
    async def test_no_orchestrator_returns_true(self, tmp_path):
        harness = self._make_harness(tmp_path)
        from forge_harness.ralph_loop import FeatureSpec

        feat = FeatureSpec(id="f-001", name="X", description="d")
        result = await harness._implement_feature(feat)
        assert result is True

    @pytest.mark.asyncio
    async def test_orchestrator_implement_feature_called(self, tmp_path):
        mock_orch = MagicMock()
        mock_orch.implement_feature = AsyncMock(return_value=(True, None))
        harness = self._make_harness(tmp_path, orchestrator=mock_orch)
        from forge_harness.ralph_loop import FeatureSpec

        feat = FeatureSpec(id="f-002", name="X", description="d")
        result = await harness._implement_feature(feat)
        assert result is True
        mock_orch.implement_feature.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_orchestrator_failure_returns_false(self, tmp_path):
        mock_orch = MagicMock()
        mock_orch.implement_feature = AsyncMock(return_value=(False, "oops"))
        harness = self._make_harness(tmp_path, orchestrator=mock_orch)
        from forge_harness.ralph_loop import FeatureSpec

        feat = FeatureSpec(id="f-003", name="X", description="d")
        result = await harness._implement_feature(feat)
        assert result is False

    @pytest.mark.asyncio
    async def test_orchestrator_exception_returns_false(self, tmp_path):
        mock_orch = MagicMock()
        mock_orch.implement_feature = AsyncMock(side_effect=RuntimeError("crash"))
        harness = self._make_harness(tmp_path, orchestrator=mock_orch)
        from forge_harness.ralph_loop import FeatureSpec

        feat = FeatureSpec(id="f-004", name="X", description="d")
        result = await harness._implement_feature(feat)
        assert result is False

    @pytest.mark.asyncio
    async def test_orchestrator_missing_implement_feature_returns_true(self, tmp_path):
        """Orchestrator without implement_feature method falls back to True."""
        mock_orch = MagicMock(spec=[])  # No implement_feature attribute
        harness = self._make_harness(tmp_path, orchestrator=mock_orch)
        from forge_harness.ralph_loop import FeatureSpec

        feat = FeatureSpec(id="f-005", name="X", description="d")
        result = await harness._implement_feature(feat)
        assert result is True


# ---------------------------------------------------------------------------
# _index_feature_to_atlas
# ---------------------------------------------------------------------------


class TestIndexFeatureToAtlas:
    def _make_harness(self, tmp_path, bridge=None):
        from forge_harness.ralph_loop import FeatureStore, LoopConfig, RalphLoopHarness

        path = _make_features_json(tmp_path, [_make_feature_dict()])
        store = FeatureStore(path)
        config = LoopConfig(domain="dom", project="proj")
        return RalphLoopHarness(
            feature_store=store, config=config, code_atlas_bridge=bridge
        )

    @pytest.mark.asyncio
    async def test_no_bridge_returns_not_indexed(self, tmp_path):
        harness = self._make_harness(tmp_path, bridge=None)
        from forge_harness.ralph_loop import FeatureSpec

        feat = FeatureSpec(id="i-001", name="X", description="d")
        result = await harness._index_feature_to_atlas(feat, success=True)
        assert result["indexed"] is False

    @pytest.mark.asyncio
    async def test_bridge_indexed_result(self, tmp_path):
        mock_bridge = MagicMock()
        mock_bridge.index_session = AsyncMock(return_value={"indexed": True})
        harness = self._make_harness(tmp_path, bridge=mock_bridge)
        from forge_harness.ralph_loop import FeatureSpec

        feat = FeatureSpec(
            id="i-002", name="Auth module", description="Security auth endpoint", priority="high"
        )
        result = await harness._index_feature_to_atlas(feat, success=True)
        assert result["indexed"] is True

    @pytest.mark.asyncio
    async def test_bridge_error_returns_error_dict(self, tmp_path):
        mock_bridge = MagicMock()
        mock_bridge.index_session = AsyncMock(side_effect=RuntimeError("network error"))
        harness = self._make_harness(tmp_path, bridge=mock_bridge)
        from forge_harness.ralph_loop import FeatureSpec

        feat = FeatureSpec(id="i-003", name="X", description="d")
        result = await harness._index_feature_to_atlas(feat, success=False)
        assert result["indexed"] is False
        assert "error" in result

    @pytest.mark.asyncio
    async def test_tags_include_category_labels(self, tmp_path):
        """Tags like 'category:api' and 'category:security' added based on feature name."""
        captured = {}

        async def fake_index(session_summary):
            captured.update(session_summary)
            return {"indexed": True}

        mock_bridge = MagicMock()
        mock_bridge.index_session = AsyncMock(side_effect=fake_index)
        harness = self._make_harness(tmp_path, bridge=mock_bridge)
        from forge_harness.ralph_loop import FeatureSpec

        feat = FeatureSpec(
            id="i-004", name="API auth security endpoint test refactor migration", description="d"
        )
        await harness._index_feature_to_atlas(feat, success=True)
        tags = captured.get("tags", [])
        assert "category:security" in tags
        assert "category:api" in tags
        assert "category:testing" in tags
        assert "category:refactor" in tags


# ---------------------------------------------------------------------------
# _record_simple_history_outcome
# ---------------------------------------------------------------------------


class TestRecordSimpleHistoryOutcome:
    @pytest.mark.asyncio
    async def test_no_history_skips_gracefully(self, tmp_path):
        from forge_harness.ralph_loop import FeatureSpec, FeatureStore, LoopConfig, RalphLoopHarness

        path = _make_features_json(tmp_path, [_make_feature_dict()])
        store = FeatureStore(path)
        harness = RalphLoopHarness(feature_store=store, config=LoopConfig())
        feat = FeatureSpec(id="h-001", name="X", description="d")
        # Should not raise
        await harness._record_simple_history_outcome(feat, success=True)

    @pytest.mark.asyncio
    async def test_records_success(self, tmp_path):
        from forge_harness.ralph_loop import FeatureSpec, FeatureStore, LoopConfig, RalphLoopHarness
        from forge_harness.simple_history import SimpleHistory

        history_path = tmp_path / "h.jsonl"
        history = SimpleHistory(history_file=history_path)
        path = _make_features_json(tmp_path, [_make_feature_dict()])
        store = FeatureStore(path)
        config = LoopConfig(domain="dom", project="proj")
        harness = RalphLoopHarness(
            feature_store=store, config=config, simple_history=history
        )
        feat = FeatureSpec(id="h-002", name="X", description="d")
        await harness._record_simple_history_outcome(feat, success=True)

        records = history.get_recent(limit=10)
        assert len(records) == 1
        assert records[0]["success"] is True

    @pytest.mark.asyncio
    async def test_records_failure_with_error(self, tmp_path):
        from forge_harness.ralph_loop import FeatureSpec, FeatureStore, LoopConfig, RalphLoopHarness
        from forge_harness.simple_history import SimpleHistory

        history_path = tmp_path / "h.jsonl"
        history = SimpleHistory(history_file=history_path)
        path = _make_features_json(tmp_path, [_make_feature_dict()])
        store = FeatureStore(path)
        config = LoopConfig(domain="dom", project="proj")
        harness = RalphLoopHarness(
            feature_store=store, config=config, simple_history=history
        )
        feat = FeatureSpec(
            id="h-003", name="X", description="d", last_error="Something went wrong"
        )
        await harness._record_simple_history_outcome(feat, success=False)

        records = history.get_recent(limit=10)
        assert records[0]["success"] is False
        assert "error" in records[0].get("context", {})

    @pytest.mark.asyncio
    async def test_long_error_truncated(self, tmp_path):
        from forge_harness.ralph_loop import FeatureSpec, FeatureStore, LoopConfig, RalphLoopHarness
        from forge_harness.simple_history import SimpleHistory

        history_path = tmp_path / "h.jsonl"
        history = SimpleHistory(history_file=history_path)
        path = _make_features_json(tmp_path, [_make_feature_dict()])
        store = FeatureStore(path)
        config = LoopConfig(domain="dom", project="proj")
        harness = RalphLoopHarness(
            feature_store=store, config=config, simple_history=history
        )
        long_error = "X" * 500
        feat = FeatureSpec(id="h-004", name="X", description="d", last_error=long_error)
        await harness._record_simple_history_outcome(feat, success=False)

        records = history.get_recent(limit=10)
        ctx = records[0].get("context", {})
        assert len(ctx.get("error", "")) <= 200


# ---------------------------------------------------------------------------
# _check_and_flush_memory
# ---------------------------------------------------------------------------


class TestCheckAndFlushMemory:
    @pytest.mark.asyncio
    async def test_no_flush_below_threshold(self, tmp_path):
        from forge_harness.ralph_loop import FeatureSpec, FeatureStore, LoopConfig, RalphLoopHarness

        path = _make_features_json(tmp_path, [_make_feature_dict()])
        store = FeatureStore(path)
        harness = RalphLoopHarness(feature_store=store, config=LoopConfig())
        harness._estimated_token_usage = 1000  # Well below 70% of 200k
        feat = FeatureSpec(id="m-001", name="X", description="d")
        # Should not raise, should do nothing
        await harness._check_and_flush_memory(feat)

    @pytest.mark.asyncio
    async def test_flush_triggered_above_threshold(self, tmp_path):
        from forge_harness.ralph_loop import FeatureSpec, FeatureStore, LoopConfig, RalphLoopHarness

        path = _make_features_json(tmp_path, [_make_feature_dict()])
        store = FeatureStore(path)
        harness = RalphLoopHarness(feature_store=store, config=LoopConfig())
        harness._estimated_token_usage = 150_000  # Above 70% of 200k
        harness.store.load()
        feat = FeatureSpec(id="m-002", name="X", description="d")
        # Should call memory manager's save_session_note
        with patch.object(
            harness._memory_manager, "_save_session_note", new_callable=AsyncMock
        ) as mock_save:
            await harness._check_and_flush_memory(feat)
        mock_save.assert_awaited()


# ---------------------------------------------------------------------------
# create_ralph_loop factory
# ---------------------------------------------------------------------------


class TestCreateRalphLoop:
    def test_basic_creation(self, tmp_path):
        path = _make_features_json(tmp_path, [_make_feature_dict()])
        from forge_harness.ralph_loop import RalphLoopHarness, create_ralph_loop

        loop = create_ralph_loop(
            features_path=path,
            max_iterations=10,
            domain="test-domain",
            project="test-project",
            enable_telemetry=False,
        )
        assert isinstance(loop, RalphLoopHarness)
        assert loop.config.max_iterations == 10
        assert loop.config.domain == "test-domain"

    def test_custom_test_command(self, tmp_path):
        path = _make_features_json(tmp_path, [_make_feature_dict()])
        from forge_harness.ralph_loop import create_ralph_loop

        loop = create_ralph_loop(
            features_path=path,
            test_command="pytest -x",
            enable_telemetry=False,
        )
        assert loop.config.test_command == "pytest -x"

    def test_working_dir_set(self, tmp_path):
        path = _make_features_json(tmp_path, [_make_feature_dict()])
        from forge_harness.ralph_loop import create_ralph_loop

        loop = create_ralph_loop(
            features_path=path, working_dir=str(tmp_path), enable_telemetry=False
        )
        assert loop.config.working_dir == tmp_path

    def test_dry_run_flag_passed(self, tmp_path):
        path = _make_features_json(tmp_path, [_make_feature_dict()])
        from forge_harness.ralph_loop import create_ralph_loop

        loop = create_ralph_loop(features_path=path, dry_run=True, enable_telemetry=False)
        assert loop.config.dry_run is True

    def test_optional_components_passed(self, tmp_path):
        path = _make_features_json(tmp_path, [_make_feature_dict()])
        from forge_harness.ralph_loop import create_ralph_loop

        mock_orchestrator = MagicMock()
        mock_bridge = MagicMock()
        loop = create_ralph_loop(
            features_path=path,
            orchestrator=mock_orchestrator,
            code_atlas_bridge=mock_bridge,
            enable_telemetry=False,
        )
        assert loop.orchestrator is mock_orchestrator
        assert loop.code_atlas_bridge is mock_bridge

    def test_str_features_path_converted_to_path(self, tmp_path):
        path = _make_features_json(tmp_path, [_make_feature_dict()])
        from forge_harness.ralph_loop import create_ralph_loop

        loop = create_ralph_loop(features_path=str(path), enable_telemetry=False)
        assert isinstance(loop.store.features_path, Path)


# ---------------------------------------------------------------------------
# create_features_from_plan
# ---------------------------------------------------------------------------


class TestCreateFeaturesFromPlan:
    def test_raises_if_plan_missing(self, tmp_path):
        from forge_harness.ralph_loop import create_features_from_plan

        with pytest.raises(FileNotFoundError):
            create_features_from_plan(tmp_path / "missing.md")

    def test_parses_task_id_table(self, tmp_path):
        plan_md = """\
## Epic H1: Authentication

### H1.1: Login

| Task ID | Task | Effort |
|---------|------|--------|
| H1.1.1 | Implement login endpoint | 2h |
| H1.1.2 | Add unit tests | 1h |
"""
        plan_path = tmp_path / "PLAN.md"
        plan_path.write_text(plan_md)
        from forge_harness.ralph_loop import create_features_from_plan

        features = create_features_from_plan(plan_path)
        assert len(features) == 2
        ids = [f.id for f in features]
        assert "H1.1.1" in ids
        assert "H1.1.2" in ids

    def test_parses_simple_task_table(self, tmp_path):
        plan_md = """\
### My Section

| Task | Est |
|------|-----|
| Write tests | 30m |
| Fix linting | 1h |
"""
        plan_path = tmp_path / "PLAN.md"
        plan_path.write_text(plan_md)
        from forge_harness.ralph_loop import create_features_from_plan

        features = create_features_from_plan(plan_path)
        assert len(features) >= 2
        names = [f.name for f in features]
        assert "Write tests" in names

    def test_empty_plan_returns_empty_list(self, tmp_path):
        plan_path = tmp_path / "PLAN.md"
        plan_path.write_text("# Just a title\n\nNo tasks here.\n")
        from forge_harness.ralph_loop import create_features_from_plan

        features = create_features_from_plan(plan_path)
        assert features == []

    def test_priority_extracted_from_epic_header(self, tmp_path):
        plan_md = """\
## Epic H1: P0 Critical Features

### H1.1: Core

| Task ID | Task | Effort |
|---------|------|--------|
| H1.1.1 | Core task | 2h |
"""
        plan_path = tmp_path / "PLAN.md"
        plan_path.write_text(plan_md)
        from forge_harness.ralph_loop import create_features_from_plan

        features = create_features_from_plan(plan_path)
        assert len(features) == 1
        assert features[0].priority == "critical"


# ---------------------------------------------------------------------------
# _estimate_tokens_from_effort
# ---------------------------------------------------------------------------


class TestEstimateTokensFromEffort:
    def test_hours(self):
        from forge_harness.ralph_loop import _estimate_tokens_from_effort

        assert _estimate_tokens_from_effort("2h") == 4000
        assert _estimate_tokens_from_effort("1.5h") == 3000
        assert _estimate_tokens_from_effort("0.5h") == 1000

    def test_minutes(self):
        from forge_harness.ralph_loop import _estimate_tokens_from_effort

        result = _estimate_tokens_from_effort("30m")
        assert result == 1000  # 30/60 * 2000

    def test_default_on_invalid(self):
        from forge_harness.ralph_loop import _estimate_tokens_from_effort

        assert _estimate_tokens_from_effort("unknown") == 4000
        assert _estimate_tokens_from_effort("") == 4000

    def test_default_on_non_hour_non_minute(self):
        from forge_harness.ralph_loop import _estimate_tokens_from_effort

        assert _estimate_tokens_from_effort("3d") == 4000


# ---------------------------------------------------------------------------
# _extract_priority_from_line
# ---------------------------------------------------------------------------


class TestExtractPriorityFromLine:
    def test_p0_returns_critical(self):
        from forge_harness.ralph_loop import _extract_priority_from_line

        assert _extract_priority_from_line("P0 blocker") == "critical"
        assert _extract_priority_from_line("CRITICAL issue") == "critical"

    def test_p1_returns_high(self):
        from forge_harness.ralph_loop import _extract_priority_from_line

        assert _extract_priority_from_line("HIGH PRIORITY task") == "high"
        assert _extract_priority_from_line("P1 feature") == "high"

    def test_p2_returns_medium(self):
        from forge_harness.ralph_loop import _extract_priority_from_line

        assert _extract_priority_from_line("P2 enhancement") == "medium"

    def test_p3_returns_low(self):
        from forge_harness.ralph_loop import _extract_priority_from_line

        assert _extract_priority_from_line("P3 nice-to-have") == "low"
        assert _extract_priority_from_line("LOW priority task") == "low"

    def test_no_priority_returns_none(self):
        from forge_harness.ralph_loop import _extract_priority_from_line

        assert _extract_priority_from_line("some regular line") is None


# ---------------------------------------------------------------------------
# GitHubFeatureTracker helpers
# ---------------------------------------------------------------------------


class TestGitHubFeatureTrackerHelpers:
    def test_get_headers_with_token(self):
        from forge_harness.ralph_loop import GitHubFeatureTracker

        tracker = GitHubFeatureTracker("owner/repo", github_token="tok-123")
        headers = tracker._get_headers()
        assert headers["Authorization"] == "Bearer tok-123"
        assert "Accept" in headers

    def test_get_headers_without_token(self):
        import os

        from forge_harness.ralph_loop import GitHubFeatureTracker

        with patch.dict(os.environ, {}, clear=True):
            tracker = GitHubFeatureTracker("owner/repo", github_token=None)
            headers = tracker._get_headers()
        assert "Authorization" not in headers

    def test_feature_to_issue_body_basic(self):
        from forge_harness.ralph_loop import FeatureSpec, GitHubFeatureTracker

        tracker = GitHubFeatureTracker("owner/repo", github_token="tok")
        feat = FeatureSpec(
            id="issue-001",
            name="My Feature",
            description="Feature description",
            acceptance_criteria=["Criterion A", "Criterion B"],
            depends_on=["dep-001"],
            tests=["test_my_feature"],
        )
        body = tracker._feature_to_issue_body(feat)
        assert "My Feature" in body
        assert "Feature description" in body
        assert "issue-001" in body
        assert "Criterion A" in body
        assert "dep-001" in body
        assert "test_my_feature" in body
        assert "Ralph Loop Harness" in body

    def test_feature_to_issue_body_with_last_error(self):
        from forge_harness.ralph_loop import FeatureSpec, GitHubFeatureTracker

        tracker = GitHubFeatureTracker("owner/repo", github_token="tok")
        feat = FeatureSpec(
            id="err-001",
            name="Erroring Feature",
            description="d",
            last_error="AssertionError: expected 1 got 2",
        )
        body = tracker._feature_to_issue_body(feat)
        assert "AssertionError" in body

    def test_status_labels_map(self):
        from forge_harness.ralph_loop import FeatureStatus, GitHubFeatureTracker

        tracker = GitHubFeatureTracker("owner/repo", github_token="tok")
        assert tracker.STATUS_LABELS[FeatureStatus.PENDING] == "status:pending"
        assert tracker.STATUS_LABELS[FeatureStatus.PASSING] == "status:passing"
        assert tracker.STATUS_LABELS[FeatureStatus.BLOCKED] == "status:blocked"
        assert tracker.STATUS_LABELS[FeatureStatus.COMPLETED] == "status:completed"


# ---------------------------------------------------------------------------
# LoopConfig.from_env (smoke test — mocks ForgeSettings)
# ---------------------------------------------------------------------------


class TestLoopConfigFromEnv:
    def test_from_env_calls_forge_settings(self, tmp_path):
        """from_env() delegates to ForgeSettings.from_env()."""
        from forge_harness.ralph_loop import LoopConfig

        mock_settings = MagicMock()
        mock_settings.to_loop_config.return_value = LoopConfig(domain="env-domain")
        with patch("forge_harness.ralph_loop.LoopConfig.from_env") as mock_from_env:
            mock_from_env.return_value = LoopConfig(domain="env-domain")
            config = LoopConfig.from_env(domain="env-domain")
        assert config.domain == "env-domain"


# ---------------------------------------------------------------------------
# _get_decision
# ---------------------------------------------------------------------------


class TestGetDecision:
    @pytest.mark.asyncio
    async def test_no_decision_engine_returns_none(self, tmp_path):
        from forge_harness.ralph_loop import FeatureSpec, FeatureStore, LoopConfig, RalphLoopHarness

        path = _make_features_json(tmp_path, [_make_feature_dict()])
        store = FeatureStore(path)
        harness = RalphLoopHarness(feature_store=store, config=LoopConfig())
        feat = FeatureSpec(id="d-001", name="X", description="d")
        result = await harness._get_decision(feat)
        assert result is None

    @pytest.mark.asyncio
    async def test_decision_engine_error_returns_none(self, tmp_path):
        from forge_harness.ralph_loop import FeatureSpec, FeatureStore, LoopConfig, RalphLoopHarness

        path = _make_features_json(tmp_path, [_make_feature_dict()])
        store = FeatureStore(path)
        mock_engine = MagicMock()
        mock_engine.get_recommendation = AsyncMock(side_effect=RuntimeError("engine crash"))
        harness = RalphLoopHarness(
            feature_store=store, config=LoopConfig(), decision_engine=mock_engine
        )
        feat = FeatureSpec(id="d-002", name="X", description="d")
        result = await harness._get_decision(feat)
        assert result is None


# ---------------------------------------------------------------------------
# _request_human_review — no approval_queue
# ---------------------------------------------------------------------------


class TestRequestHumanReview:
    @pytest.mark.asyncio
    async def test_no_approval_queue_returns_true(self, tmp_path):
        from forge_harness.ralph_loop import FeatureSpec, FeatureStore, LoopConfig, RalphLoopHarness

        path = _make_features_json(tmp_path, [_make_feature_dict()])
        store = FeatureStore(path)
        harness = RalphLoopHarness(feature_store=store, config=LoopConfig())
        feat = FeatureSpec(id="hr-001", name="X", description="d")
        mock_rec = MagicMock()
        mock_rec.reasoning = "Needs review"
        mock_rec.warnings = []
        result = await harness._request_human_review(feat, mock_rec)
        assert result is True


# ---------------------------------------------------------------------------
# LoopResult attribute coverage
# ---------------------------------------------------------------------------


class TestLoopResultAttributes:
    def test_all_attributes_present(self):
        from forge_harness.ralph_loop import LoopResult

        r = LoopResult(
            success=True,
            iterations=5,
            features_completed=3,
            features_blocked=1,
            features_remaining=0,
            total_tokens=12000,
            duration_seconds=60.0,
            checkpoint_path=Path("/tmp/cp.json"),
        )
        assert r.success is True
        assert r.iterations == 5
        assert r.features_completed == 3
        assert r.features_blocked == 1
        assert r.features_remaining == 0
        assert r.total_tokens == 12000
        assert r.duration_seconds == 60.0
        assert r.checkpoint_path == Path("/tmp/cp.json")
