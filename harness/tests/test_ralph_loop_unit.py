"""
Pure unit tests for forge_harness.ralph_loop

Covers:
- FeatureStatus enum
- FeatureSpec dataclass initialization, to_dict, from_dict
- LoopConfig dataclass initialization and factory methods
- LoopResult dataclass
- FeatureStore initialization
- RalphLoopHarness initialization
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge_harness.ralph_loop import (
    FeatureSpec,
    FeatureStatus,
    FeatureStore,
    LoopConfig,
    LoopResult,
    RalphLoopHarness,
)


class TestFeatureStatus:
    """Tests for FeatureStatus enum."""

    def test_enum_values(self):
        """Test enum value strings."""
        assert FeatureStatus.PENDING.value == "pending"
        assert FeatureStatus.IN_PROGRESS.value == "in_progress"
        assert FeatureStatus.PASSING.value == "passing"
        assert FeatureStatus.FAILING.value == "failing"
        assert FeatureStatus.BLOCKED.value == "blocked"
        assert FeatureStatus.SKIPPED.value == "skipped"
        assert FeatureStatus.COMPLETED.value == "completed"


class TestFeatureSpec:
    """Tests for FeatureSpec dataclass."""

    def test_init_defaults(self):
        """Test default initialization."""
        spec = FeatureSpec(
            id="feat-001",
            name="Test Feature",
            description="Test description",
        )

        assert spec.id == "feat-001"
        assert spec.name == "Test Feature"
        assert spec.description == "Test description"
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

    def test_init_custom_values(self):
        """Test initialization with custom values."""
        spec = FeatureSpec(
            id="feat-001",
            name="Test Feature",
            description="Test description",
            status=FeatureStatus.IN_PROGRESS,
            priority="high",
            acceptance_criteria=["Criteria 1", "Criteria 2"],
            depends_on=["feat-000"],
            tests=["test_feature"],
            estimated_tokens=8000,
            attempts=2,
            last_error="Previous error",
            files_to_create=["new_file.py"],
            files_to_modify=["existing_file.py"],
            category="authentication",
            tags=["security", "api"],
        )

        assert spec.status == FeatureStatus.IN_PROGRESS
        assert spec.priority == "high"
        assert spec.acceptance_criteria == ["Criteria 1", "Criteria 2"]
        assert spec.depends_on == ["feat-000"]
        assert spec.tests == ["test_feature"]
        assert spec.estimated_tokens == 8000
        assert spec.attempts == 2
        assert spec.last_error == "Previous error"
        assert spec.files_to_create == ["new_file.py"]
        assert spec.files_to_modify == ["existing_file.py"]
        assert spec.category == "authentication"
        assert spec.tags == ["security", "api"]

    def test_to_dict_basic(self):
        """Test basic dictionary conversion."""
        spec = FeatureSpec(
            id="feat-001",
            name="Test Feature",
            description="Test description",
            status=FeatureStatus.PASSING,
        )

        data = spec.to_dict()

        assert data["id"] == "feat-001"
        assert data["name"] == "Test Feature"
        assert data["description"] == "Test description"
        assert data["status"] == "passing"
        assert data["priority"] == "medium"
        assert "files_to_create" not in data  # Empty lists omitted

    def test_to_dict_includes_optional_fields(self):
        """Test dict includes optional fields when set."""
        spec = FeatureSpec(
            id="feat-001",
            name="Test",
            description="Test",
            files_to_create=["file1.py"],
            files_to_modify=["file2.py"],
            category="api",
            tags=["tag1"],
        )

        data = spec.to_dict()

        assert data["files_to_create"] == ["file1.py"]
        assert data["files_to_modify"] == ["file2.py"]
        assert data["category"] == "api"
        assert data["tags"] == ["tag1"]

    def test_from_dict_basic(self):
        """Test creation from basic dictionary."""
        data = {
            "id": "feat-001",
            "name": "Test Feature",
            "description": "Test description",
            "status": "in_progress",
            "priority": "high",
        }

        spec = FeatureSpec.from_dict(data)

        assert spec.id == "feat-001"
        assert spec.name == "Test Feature"
        assert spec.status == FeatureStatus.IN_PROGRESS
        assert spec.priority == "high"

    def test_from_dict_accepts_title(self):
        """Test creation accepts 'title' as alternative to 'name'."""
        data = {
            "id": "feat-001",
            "title": "Test Title",
            "description": "Test description",
        }

        spec = FeatureSpec.from_dict(data)

        assert spec.name == "Test Title"

    def test_from_dict_accepts_dependencies(self):
        """Test creation accepts 'dependencies' as alternative to 'depends_on'."""
        data = {
            "id": "feat-001",
            "name": "Test",
            "description": "Test",
            "dependencies": ["feat-000", "feat-001"],
        }

        spec = FeatureSpec.from_dict(data)

        assert spec.depends_on == ["feat-000", "feat-001"]

    def test_from_dict_missing_name_raises(self):
        """Test creation raises when name/title is missing."""
        data = {
            "id": "feat-001",
            "description": "Test description",
        }

        with pytest.raises(KeyError):
            FeatureSpec.from_dict(data)


class TestLoopConfig:
    """Tests for LoopConfig dataclass."""

    def test_default_values(self):
        """Test default configuration values."""
        config = LoopConfig()

        assert config.domain is None
        assert config.project is None
        assert config.max_iterations == 100
        assert config.max_failures_per_feature == 5
        assert config.checkpoint_interval == 10
        assert config.test_command == "uv run pytest tests/ -v --tb=short -x"
        assert config.lint_command is None
        assert config.timeout_seconds == 3600
        assert config.dry_run is False
        assert config.approval_timeout_hours == 24.0
        assert config.history_threshold_high == 0.7
        assert config.history_threshold_low == 0.3
        assert config.cross_domain_enabled is True

    def test_custom_values(self):
        """Test custom configuration values."""
        config = LoopConfig(
            domain="codeswiftr-com",
            project="interview-simulator",
            max_iterations=50,
            dry_run=True,
        )

        assert config.domain == "codeswiftr-com"
        assert config.project == "interview-simulator"
        assert config.max_iterations == 50
        assert config.dry_run is True

    def test_from_env_exists(self):
        """Test that from_env method exists."""
        assert hasattr(LoopConfig, 'from_env')
        assert callable(LoopConfig.from_env)

    def test_from_settings_exists(self):
        """Test that from_settings method exists."""
        assert hasattr(LoopConfig, 'from_settings')
        assert callable(LoopConfig.from_settings)


class TestLoopResult:
    """Tests for LoopResult dataclass."""

    def test_basic_result(self):
        """Test basic result creation."""
        result = LoopResult(
            success=True,
            iterations=10,
            features_completed=8,
            features_blocked=1,
            features_remaining=1,
            total_tokens=40000,
            duration_seconds=300.0,
        )

        assert result.success is True
        assert result.iterations == 10
        assert result.features_completed == 8
        assert result.features_blocked == 1
        assert result.features_remaining == 1
        assert result.total_tokens == 40000
        assert result.duration_seconds == 300.0
        assert result.checkpoint_path is None

    def test_result_with_checkpoint(self):
        """Test result with checkpoint path."""
        result = LoopResult(
            success=True,
            iterations=10,
            features_completed=10,
            features_blocked=0,
            features_remaining=0,
            total_tokens=50000,
            duration_seconds=600.0,
            checkpoint_path=Path("/checkpoints/ckpt.json"),
        )

        assert result.checkpoint_path == Path("/checkpoints/ckpt.json")


class TestFeatureStore:
    """Tests for FeatureStore class."""

    @pytest.fixture
    def temp_features_file(self, tmp_path):
        """Create a temporary features file."""
        features_path = tmp_path / "features.json"
        features_data = {
            "version": "1.0",
            "features": [
                {
                    "id": "feat-001",
                    "name": "Feature One",
                    "description": "First feature",
                    "priority": "high",
                    "status": "pending",
                },
                {
                    "id": "feat-002",
                    "name": "Feature Two",
                    "description": "Second feature",
                    "priority": "medium",
                    "status": "passing",
                },
                {
                    "id": "feat-003",
                    "name": "Feature Three",
                    "description": "Third feature",
                    "priority": "critical",
                    "status": "pending",
                },
            ],
        }
        features_path.write_text(json.dumps(features_data))
        return features_path

    @pytest.fixture
    def temp_features_list_file(self, tmp_path):
        """Create a temporary features file with list format."""
        features_path = tmp_path / "features_list.json"
        features_data = [
            {
                "id": "feat-001",
                "name": "Feature One",
                "description": "First feature",
                "priority": "high",
            },
        ]
        features_path.write_text(json.dumps(features_data))
        return features_path

    def test_init(self, temp_features_file):
        """Test store initialization."""
        store = FeatureStore(temp_features_file)

        assert store.features_path == temp_features_file

    def test_load_with_wrapper_format(self, temp_features_file):
        """Test loading features with wrapper format."""
        store = FeatureStore(temp_features_file)

        features = store.load()

        assert len(features) == 3
        ids = {f.id for f in features}
        assert ids == {"feat-001", "feat-002", "feat-003"}

    def test_load_with_list_format(self, temp_features_list_file):
        """Test loading features with list format."""
        store = FeatureStore(temp_features_list_file)

        features = store.load()

        assert len(features) == 1
        assert features[0].id == "feat-001"

    def test_load_sorts_by_priority(self, temp_features_file):
        """Test features are sorted by priority."""
        store = FeatureStore(temp_features_file)

        features = store.load()

        # Critical should be first, then high, then medium
        assert features[0].id == "feat-003"  # critical
        assert features[1].id == "feat-001"  # high
        assert features[2].id == "feat-002"  # medium

    def test_load_missing_file(self, tmp_path):
        """Test loading from missing file."""
        store = FeatureStore(tmp_path / "nonexistent.json")

        features = store.load()

        assert features == []

    def test_load_invalid_json(self, tmp_path):
        """Test loading invalid JSON."""
        features_path = tmp_path / "invalid.json"
        features_path.write_text("not valid json")

        store = FeatureStore(features_path)
        features = store.load()

        assert features == []

    def test_get_existing(self, temp_features_file):
        """Test getting existing feature."""
        store = FeatureStore(temp_features_file)
        store.load()

        feature = store.get("feat-001")

        assert feature is not None
        assert feature.id == "feat-001"
        assert feature.name == "Feature One"

    def test_get_missing(self, temp_features_file):
        """Test getting missing feature."""
        store = FeatureStore(temp_features_file)
        store.load()

        feature = store.get("nonexistent")

        assert feature is None

    def test_update(self, temp_features_file):
        """Test updating a feature."""
        store = FeatureStore(temp_features_file)
        store.load()

        feature = store.get("feat-001")
        feature.status = FeatureStatus.IN_PROGRESS
        feature.attempts = 1

        store.update(feature)

        updated = store.get("feat-001")
        assert updated.status == FeatureStatus.IN_PROGRESS
        assert updated.attempts == 1

    def test_get_stats(self, temp_features_file):
        """Test getting feature statistics."""
        store = FeatureStore(temp_features_file)
        store.load()

        stats = store.get_stats()

        assert stats["pending"] == 2
        assert stats["passing"] == 1
        assert stats["in_progress"] == 0
        assert stats["failing"] == 0
        assert stats["blocked"] == 0

    def test_save(self, tmp_path):
        """Test saving features."""
        features_path = tmp_path / "features.json"
        store = FeatureStore(features_path)

        # Load empty
        store.load()

        # Add a feature
        feature = FeatureSpec(
            id="feat-001",
            name="Test Feature",
            description="Test",
            status=FeatureStatus.PASSING,
        )
        store.update(feature)

        # Save
        store.save()

        # Verify file exists and has correct format
        assert features_path.exists()
        data = json.loads(features_path.read_text())
        assert data["version"] == "1.0"
        assert len(data["features"]) == 1
        assert data["features"][0]["id"] == "feat-001"
        assert data["features"][0]["status"] == "passing"


class TestRalphLoopHarnessInit:
    """Tests for RalphLoopHarness initialization."""

    @pytest.fixture
    def temp_store(self, tmp_path):
        """Create temporary feature store."""
        features_path = tmp_path / "features.json"
        features_path.write_text('{"version": "1.0", "features": []}')
        store = FeatureStore(features_path)
        return store

    def test_init_basic(self, temp_store):
        """Test basic initialization."""
        config = LoopConfig(domain="test-domain", project="test-project")

        harness = RalphLoopHarness(
            feature_store=temp_store,
            config=config,
        )

        assert harness.store is temp_store
        assert harness.config is config
        assert harness._iteration == 0
        assert harness._start_time is None

    def test_init_with_optional_deps(self, temp_store):
        """Test initialization with optional dependencies."""
        config = LoopConfig(domain="test-domain", project="test-project")
        orchestrator = MagicMock()
        decision_engine = MagicMock()
        approval_queue = MagicMock()
        telemetry = MagicMock()
        simple_history = MagicMock()

        harness = RalphLoopHarness(
            feature_store=temp_store,
            config=config,
            orchestrator=orchestrator,
            decision_engine=decision_engine,
            approval_queue=approval_queue,
            telemetry=telemetry,
            simple_history=simple_history,
        )

        assert harness.orchestrator is orchestrator
        assert harness.decision_engine is decision_engine
        assert harness.approval_queue is approval_queue
        assert harness.telemetry is telemetry
        assert harness.simple_history is simple_history

    def test_extract_feature_type_with_category(self, temp_store):
        """Test feature type extraction with category."""
        config = LoopConfig(domain="test-domain", project="test-project")
        harness = RalphLoopHarness(
            feature_store=temp_store,
            config=config,
        )

        feature = FeatureSpec(
            id="feat-001",
            name="Test",
            description="Test",
            category="authentication",
        )

        feature_type = harness._extract_feature_type(feature)

        assert feature_type == "authentication"

    def test_extract_feature_type_with_tags(self, temp_store):
        """Test feature type extraction with tags."""
        config = LoopConfig(domain="test-domain", project="test-project")
        harness = RalphLoopHarness(
            feature_store=temp_store,
            config=config,
        )

        feature = FeatureSpec(
            id="feat-001",
            name="Test",
            description="Test",
            tags=["api", "security"],
        )

        feature_type = harness._extract_feature_type(feature)

        assert "api_endpoint" in feature_type  # First tag "api" gets normalized

    def test_extract_feature_type_from_name(self, temp_store):
        """Test feature type extraction from name/description."""
        config = LoopConfig(domain="test-domain", project="test-project")
        harness = RalphLoopHarness(
            feature_store=temp_store,
            config=config,
        )

        feature = FeatureSpec(
            id="feat-001",
            name="Auth Implementation",
            description="Add authentication with JWT tokens",
        )

        feature_type = harness._extract_feature_type(feature)

        assert feature_type == "authentication"

    def test_extract_feature_type_from_id(self, temp_store):
        """Test feature type extraction from ID prefix."""
        config = LoopConfig(domain="test-domain", project="test-project")
        harness = RalphLoopHarness(
            feature_store=temp_store,
            config=config,
        )

        feature = FeatureSpec(
            id="api-endpoint-001",
            name="Test API",
            description="Test",
        )

        feature_type = harness._extract_feature_type(feature)

        # ID prefix is used as fallback when no other indicators present
        assert feature_type is not None

    def test_extract_file_paths_from_tests(self, temp_store):
        """Test file path extraction from tests field."""
        config = LoopConfig(domain="test-domain", project="test-project")
        harness = RalphLoopHarness(
            feature_store=temp_store,
            config=config,
        )

        feature = FeatureSpec(
            id="feat-001",
            name="Test",
            description="Test",
            tests=["tests/test_feature.py", "src/module.py"],
        )

        paths = harness._extract_file_paths(feature)

        assert "tests/test_feature.py" in paths
        assert "src/module.py" in paths

    def test_extract_file_paths_from_description(self, temp_store):
        """Test file path extraction from description."""
        config = LoopConfig(domain="test-domain", project="test-project")
        harness = RalphLoopHarness(
            feature_store=temp_store,
            config=config,
        )

        feature = FeatureSpec(
            id="feat-001",
            name="Test",
            description="Modify `src/auth.py` and update `tests/test_auth.py`",
        )

        paths = harness._extract_file_paths(feature)

        assert "src/auth.py" in paths
        assert "tests/test_auth.py" in paths
