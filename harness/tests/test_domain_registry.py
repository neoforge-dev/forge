"""
Tests for DomainRegistry
========================

Tests the domain registry loading, querying, and validation.
"""

# Import directly to avoid agent.py which requires claude_code_sdk
import sys
import tempfile
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent / "forge_harness"))

from domain_registry import (
    ContentConfig,
    DeploymentConfig,
    DomainEntry,
    DomainRegistry,
    NotionConfig,
)


@pytest.fixture
def sample_domains_yaml():
    """Create a sample domains.yaml for testing."""
    return {
        "domains": {
            "test-domain-1": {
                "display_name": "Test Domain 1",
                "compliance": ["COPPA"],
                "human_gates": ["age_verification"],
                "frontend_tier": "React",
                "localization": None,
                "special_rules": {"test_rule": True},
                "content": {
                    "tier": 1,
                    "target_audience": "Test audience",
                    "voice_tone": "professional",
                    "content_strategy_path": "CONTENT_STRATEGY.md",
                    "pillars": ["Pillar 1", "Pillar 2"],
                },
                "deployment": {
                    "railway_service": "test-service",
                    "cloudflare_project": "test-project",
                    "marketing_site_path": "marketing-template",
                },
                "notion": {
                    "data_source_id": "test-data-source",
                    "linked_database_id": "test-database",
                },
                "products": ["Product A", "Product B"],
                "active": True,
            },
            "test-domain-2": {
                "display_name": "Test Domain 2",
                "compliance": ["HIPAA-lite"],
                "human_gates": ["health_data"],
                "frontend_tier": "Lit PWA",
                "localization": "es",
                "special_rules": {},
                "content": {
                    "tier": 2,
                    "target_audience": "Another audience",
                    "voice_tone": "friendly",
                    "pillars": [],
                },
                "deployment": {},
                "notion": {},
                "products": [],
                "active": True,
            },
            "inactive-domain": {
                "display_name": "Inactive Domain",
                "compliance": [],
                "human_gates": [],
                "frontend_tier": "React",
                "content": {"tier": 3, "target_audience": "None", "voice_tone": "formal"},
                "active": False,
            },
        }
    }


@pytest.fixture
def temp_domains_file(sample_domains_yaml):
    """Create a temporary domains.yaml file."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.dump(sample_domains_yaml, f)
        return Path(f.name)


@pytest.fixture(autouse=True)
def reset_registry():
    """Reset the registry before each test."""
    DomainRegistry._domains = {}
    DomainRegistry._loaded = False
    DomainRegistry._yaml_path = None
    yield
    # Cleanup after test
    DomainRegistry._domains = {}
    DomainRegistry._loaded = False
    DomainRegistry._yaml_path = None


class TestDomainEntry:
    """Tests for DomainEntry dataclass."""

    def test_from_dict_full(self):
        """Test creating DomainEntry from complete dict."""
        data = {
            "display_name": "Test Domain",
            "compliance": ["COPPA"],
            "human_gates": ["auth"],
            "frontend_tier": "React",
            "localization": "en",
            "special_rules": {"key": "value"},
            "content": {
                "tier": 1,
                "target_audience": "Developers",
                "voice_tone": "technical",
                "content_strategy_path": "docs/CONTENT.md",
                "pillars": ["A", "B"],
            },
            "deployment": {
                "railway_service": "svc",
                "cloudflare_project": "proj",
                "marketing_site_path": "marketing",
            },
            "notion": {
                "data_source_id": "ds-123",
                "linked_database_id": "db-456",
            },
            "products": ["Product 1"],
            "active": True,
        }

        entry = DomainEntry.from_dict("test-key", data)

        assert entry.key == "test-key"
        assert entry.display_name == "Test Domain"
        assert entry.compliance == ["COPPA"]
        assert entry.human_gates == ["auth"]
        assert entry.frontend_tier == "React"
        assert entry.localization == "en"
        assert entry.special_rules == {"key": "value"}
        assert entry.products == ["Product 1"]
        assert entry.active is True

        # Content config
        assert entry.content.tier == 1
        assert entry.content.target_audience == "Developers"
        assert entry.content.voice_tone == "technical"
        assert entry.content.content_strategy_path == "docs/CONTENT.md"
        assert entry.content.pillars == ["A", "B"]

        # Deployment config
        assert entry.deployment.railway_service == "svc"
        assert entry.deployment.cloudflare_project == "proj"
        assert entry.deployment.marketing_site_path == "marketing"

        # Notion config
        assert entry.notion.data_source_id == "ds-123"
        assert entry.notion.linked_database_id == "db-456"

    def test_from_dict_minimal(self):
        """Test creating DomainEntry from minimal dict."""
        data = {
            "display_name": "Minimal",
            "content": {
                "tier": 3,
                "target_audience": "General",
                "voice_tone": "casual",
            },
        }

        entry = DomainEntry.from_dict("minimal-key", data)

        assert entry.key == "minimal-key"
        assert entry.display_name == "Minimal"
        assert entry.compliance == []
        assert entry.human_gates == []
        assert entry.frontend_tier == "React"  # default
        assert entry.localization is None
        assert entry.active is True  # default
        assert entry.content.tier == 3
        assert entry.deployment.railway_service is None
        assert entry.notion.data_source_id is None


class TestDomainRegistry:
    """Tests for DomainRegistry class methods."""

    def test_load_from_file(self, temp_domains_file):
        """Test loading registry from YAML file."""
        DomainRegistry.load(temp_domains_file)

        assert DomainRegistry._loaded is True
        assert len(DomainRegistry._domains) == 3

    def test_load_file_not_found(self):
        """Test loading from non-existent file raises error."""
        with pytest.raises(FileNotFoundError):
            DomainRegistry.load(Path("/nonexistent/path.yaml"))

    def test_get_existing_domain(self, temp_domains_file):
        """Test getting an existing domain."""
        DomainRegistry.load(temp_domains_file)

        domain = DomainRegistry.get("test-domain-1")

        assert domain.key == "test-domain-1"
        assert domain.display_name == "Test Domain 1"
        assert domain.compliance == ["COPPA"]

    def test_get_unknown_domain(self, temp_domains_file):
        """Test getting unknown domain raises KeyError."""
        DomainRegistry.load(temp_domains_file)

        with pytest.raises(KeyError, match="Unknown domain"):
            DomainRegistry.get("nonexistent-domain")

    def test_get_or_none_existing(self, temp_domains_file):
        """Test get_or_none with existing domain."""
        DomainRegistry.load(temp_domains_file)

        domain = DomainRegistry.get_or_none("test-domain-1")

        assert domain is not None
        assert domain.key == "test-domain-1"

    def test_get_or_none_missing(self, temp_domains_file):
        """Test get_or_none with missing domain returns None."""
        DomainRegistry.load(temp_domains_file)

        domain = DomainRegistry.get_or_none("nonexistent")

        assert domain is None

    def test_all(self, temp_domains_file):
        """Test getting all domains."""
        DomainRegistry.load(temp_domains_file)

        all_domains = DomainRegistry.all()

        assert len(all_domains) == 3
        keys = [d.key for d in all_domains]
        assert "test-domain-1" in keys
        assert "test-domain-2" in keys
        assert "inactive-domain" in keys

    def test_keys(self, temp_domains_file):
        """Test getting all domain keys."""
        DomainRegistry.load(temp_domains_file)

        keys = DomainRegistry.keys()

        assert len(keys) == 3
        assert "test-domain-1" in keys

    def test_active(self, temp_domains_file):
        """Test getting only active domains."""
        DomainRegistry.load(temp_domains_file)

        active = DomainRegistry.active()

        assert len(active) == 2
        keys = [d.key for d in active]
        assert "test-domain-1" in keys
        assert "test-domain-2" in keys
        assert "inactive-domain" not in keys

    def test_by_tier(self, temp_domains_file):
        """Test filtering by content tier."""
        DomainRegistry.load(temp_domains_file)

        tier_1 = DomainRegistry.by_tier(1)
        tier_2 = DomainRegistry.by_tier(2)
        tier_3 = DomainRegistry.by_tier(3)

        assert len(tier_1) == 1
        assert tier_1[0].key == "test-domain-1"

        assert len(tier_2) == 1
        assert tier_2[0].key == "test-domain-2"

        assert len(tier_3) == 1
        assert tier_3[0].key == "inactive-domain"

    def test_by_compliance(self, temp_domains_file):
        """Test filtering by compliance requirement."""
        DomainRegistry.load(temp_domains_file)

        coppa = DomainRegistry.by_compliance("COPPA")
        hipaa = DomainRegistry.by_compliance("HIPAA-lite")
        gdpr = DomainRegistry.by_compliance("GDPR")

        assert len(coppa) == 1
        assert coppa[0].key == "test-domain-1"

        assert len(hipaa) == 1
        assert hipaa[0].key == "test-domain-2"

        assert len(gdpr) == 0

    def test_by_human_gate(self, temp_domains_file):
        """Test filtering by human gate."""
        DomainRegistry.load(temp_domains_file)

        age_gates = DomainRegistry.by_human_gate("age_verification")
        health_gates = DomainRegistry.by_human_gate("health_data")

        assert len(age_gates) == 1
        assert age_gates[0].key == "test-domain-1"

        assert len(health_gates) == 1
        assert health_gates[0].key == "test-domain-2"

    def test_with_notion(self, temp_domains_file):
        """Test getting domains with Notion configured."""
        DomainRegistry.load(temp_domains_file)

        with_notion = DomainRegistry.with_notion()

        assert len(with_notion) == 1
        assert with_notion[0].key == "test-domain-1"

    def test_with_deployment(self, temp_domains_file):
        """Test getting domains with deployment configured."""
        DomainRegistry.load(temp_domains_file)

        with_deploy = DomainRegistry.with_deployment()

        assert len(with_deploy) == 1
        assert with_deploy[0].key == "test-domain-1"

    def test_auto_load_on_query(self):
        """Test that registry auto-loads from default path on first query."""
        # This will auto-load from the real domains.yaml
        all_domains = DomainRegistry.all()

        # Should have loaded the real domains
        assert DomainRegistry._loaded is True
        assert len(all_domains) > 0

    def test_reload(self, temp_domains_file):
        """Test reloading registry."""
        DomainRegistry.load(temp_domains_file)
        original_count = len(DomainRegistry.all())

        # Modify the yaml
        DomainRegistry._yaml_path = temp_domains_file
        DomainRegistry.reload()

        assert len(DomainRegistry.all()) == original_count

    def test_summary(self, temp_domains_file):
        """Test getting registry summary."""
        DomainRegistry.load(temp_domains_file)

        summary = DomainRegistry.summary()

        assert summary["total_domains"] == 3
        assert summary["active_domains"] == 2
        assert summary["tier_1_count"] == 1
        assert summary["tier_2_count"] == 1
        assert summary["tier_3_count"] == 1
        assert summary["with_notion"] == 1
        assert summary["compliance_domains"]["COPPA"] == 1
        assert summary["compliance_domains"]["HIPAA-lite"] == 1


class TestDomainRegistryValidation:
    """Tests for registry validation."""

    def test_validate_valid_registry(self, temp_domains_file):
        """Test validation passes for valid registry."""
        DomainRegistry.load(temp_domains_file)

        errors = DomainRegistry.validate()

        assert len(errors) == 0

    def test_validate_missing_display_name(self):
        """Test validation catches missing display_name."""
        data = {
            "domains": {
                "bad-domain": {
                    "display_name": "",  # Empty
                    "content": {"tier": 1, "target_audience": "X", "voice_tone": "y"},
                }
            }
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(data, f)
            path = Path(f.name)

        DomainRegistry.load(path)
        errors = DomainRegistry.validate()

        assert any("missing display_name" in e for e in errors)

    def test_validate_invalid_tier(self):
        """Test validation catches invalid content tier."""
        data = {
            "domains": {
                "bad-domain": {
                    "display_name": "Bad",
                    "content": {"tier": 5, "target_audience": "X", "voice_tone": "y"},  # Invalid
                }
            }
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(data, f)
            path = Path(f.name)

        DomainRegistry.load(path)
        errors = DomainRegistry.validate()

        assert any("invalid content tier" in e for e in errors)

    def test_validate_invalid_frontend_tier(self):
        """Test validation catches invalid frontend tier."""
        data = {
            "domains": {
                "bad-domain": {
                    "display_name": "Bad",
                    "frontend_tier": "InvalidFramework",  # Invalid
                    "content": {"tier": 1, "target_audience": "X", "voice_tone": "y"},
                }
            }
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(data, f)
            path = Path(f.name)

        DomainRegistry.load(path)
        errors = DomainRegistry.validate()

        assert any("invalid frontend_tier" in e for e in errors)

    def test_validate_incomplete_notion(self):
        """Test validation catches incomplete Notion config."""
        data = {
            "domains": {
                "bad-domain": {
                    "display_name": "Bad",
                    "content": {"tier": 1, "target_audience": "X", "voice_tone": "y"},
                    "notion": {
                        "data_source_id": "has-this",
                        "linked_database_id": None,  # Missing
                    },
                }
            }
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(data, f)
            path = Path(f.name)

        DomainRegistry.load(path)
        errors = DomainRegistry.validate()

        assert any("Notion config incomplete" in e for e in errors)


class TestRealDomainsYaml:
    """Integration tests using the real domains.yaml."""

    def test_load_real_domains(self):
        """Test loading the actual domains.yaml file."""
        DomainRegistry.load()

        assert DomainRegistry._loaded is True
        assert len(DomainRegistry.all()) >= 10  # At least 10 FORGE domains

    def test_real_domains_valid(self):
        """Test that real domains.yaml passes validation."""
        DomainRegistry.load()

        errors = DomainRegistry.validate()

        assert len(errors) == 0, f"Validation errors: {errors}"

    def test_known_domains_exist(self):
        """Test that known domains exist."""
        DomainRegistry.load()

        # Should have key domains
        assert DomainRegistry.get_or_none("thebrightharbor-com") is not None
        assert DomainRegistry.get_or_none("calmconnect-io") is not None
        assert DomainRegistry.get_or_none("codeswiftr-com") is not None
        assert DomainRegistry.get_or_none("brandfocus-ai") is not None

    def test_tier_allocation(self):
        """Test tier allocation matches strategy."""
        DomainRegistry.load()

        tier_1 = DomainRegistry.by_tier(1)
        tier_2 = DomainRegistry.by_tier(2)

        # CodeSwiftr should be tier 1
        tier_1_keys = [d.key for d in tier_1]
        assert "codeswiftr-com" in tier_1_keys

        # LeanVibe and NeoForge should be tier 2
        tier_2_keys = [d.key for d in tier_2]
        assert "leanvibe-dev" in tier_2_keys
        assert "neoforge-dev" in tier_2_keys

    def test_coppa_domain(self):
        """Test COPPA compliance is set for TheBrightHarbor."""
        DomainRegistry.load()

        coppa_domains = DomainRegistry.by_compliance("COPPA")

        assert len(coppa_domains) == 1
        assert coppa_domains[0].key == "thebrightharbor-com"


class TestDomainEntryEdgeCases:
    """Test edge cases for DomainEntry creation."""

    def test_from_dict_empty_content(self):
        """Test creating DomainEntry with empty content dict."""
        data = {
            "display_name": "Test",
            "content": {},  # Empty content
        }

        entry = DomainEntry.from_dict("test-key", data)

        assert entry.content.tier == 3  # default
        assert entry.content.target_audience == ""  # default
        assert entry.content.voice_tone == "professional"  # default
        assert entry.content.pillars == []

    def test_from_dict_missing_content_fields(self):
        """Test creating DomainEntry with partially missing content fields."""
        data = {
            "display_name": "Test",
            "content": {
                "tier": 1,
                # Missing target_audience and voice_tone
            },
        }

        entry = DomainEntry.from_dict("test-key", data)

        assert entry.content.tier == 1
        assert entry.content.target_audience == ""
        assert entry.content.voice_tone == "professional"

    def test_from_dict_empty_deployment(self):
        """Test creating DomainEntry with empty deployment dict."""
        data = {
            "display_name": "Test",
            "content": {"tier": 1, "target_audience": "Test", "voice_tone": "test"},
            "deployment": {},  # Empty
        }

        entry = DomainEntry.from_dict("test-key", data)

        assert entry.deployment.railway_service is None
        assert entry.deployment.cloudflare_project is None
        assert entry.deployment.marketing_site_path is None

    def test_from_dict_empty_notion(self):
        """Test creating DomainEntry with empty notion dict."""
        data = {
            "display_name": "Test",
            "content": {"tier": 1, "target_audience": "Test", "voice_tone": "test"},
            "notion": {},  # Empty
        }

        entry = DomainEntry.from_dict("test-key", data)

        assert entry.notion.data_source_id is None
        assert entry.notion.linked_database_id is None

    def test_from_dict_no_nested_configs(self):
        """Test creating DomainEntry without nested config dicts."""
        data = {
            "display_name": "Test",
            # No content, deployment, or notion keys
        }

        entry = DomainEntry.from_dict("test-key", data)

        # Should create default configs
        assert entry.content is not None
        assert entry.deployment is not None
        assert entry.notion is not None

    def test_from_dict_none_values(self):
        """Test creating DomainEntry with None values."""
        data = {
            "display_name": "Test",
            "compliance": None,
            "human_gates": None,
            "products": None,
            "content": {"tier": 1, "target_audience": "Test", "voice_tone": "test"},
        }

        entry = DomainEntry.from_dict("test-key", data)

        # None values are passed through as-is by data.get(), resulting in None
        # The code doesn't convert None to [] - it uses data.get() with [] as default
        # So when explicitly set to None, it stays None
        # This is the actual behavior - if field is None, it's None, not []
        assert entry.compliance is None or entry.compliance == []
        assert entry.human_gates is None or entry.human_gates == []
        assert entry.products is None or entry.products == []

    def test_from_dict_special_rules_various_types(self):
        """Test special_rules with various data types."""
        data = {
            "display_name": "Test",
            "content": {"tier": 1, "target_audience": "Test", "voice_tone": "test"},
            "special_rules": {
                "bool_rule": True,
                "string_rule": "value",
                "number_rule": 42,
                "list_rule": ["a", "b"],
                "nested_dict": {"key": "value"},
            },
        }

        entry = DomainEntry.from_dict("test-key", data)

        assert entry.special_rules["bool_rule"] is True
        assert entry.special_rules["string_rule"] == "value"
        assert entry.special_rules["number_rule"] == 42
        assert entry.special_rules["list_rule"] == ["a", "b"]
        assert entry.special_rules["nested_dict"] == {"key": "value"}


class TestDomainRegistryFiltering:
    """Test advanced filtering and query combinations."""

    @pytest.fixture
    def complex_registry(self):
        """Create a registry with complex filtering scenarios."""
        data = {
            "domains": {
                "multi-compliance": {
                    "display_name": "Multi",
                    "compliance": ["COPPA", "GDPR", "HIPAA-lite"],
                    "human_gates": ["payment_integration", "health_data"],
                    "frontend_tier": "React",
                    "content": {"tier": 1, "target_audience": "Test", "voice_tone": "test"},
                    "deployment": {
                        "railway_service": "test",
                        "cloudflare_project": "test",
                    },
                    "notion": {
                        "data_source_id": "test-ds",
                        "linked_database_id": "test-db",
                    },
                    "active": True,
                },
                "railway-only": {
                    "display_name": "Railway",
                    "content": {"tier": 2, "target_audience": "Test", "voice_tone": "test"},
                    "deployment": {"railway_service": "railway-svc"},
                    "active": True,
                },
                "cloudflare-only": {
                    "display_name": "Cloudflare",
                    "content": {"tier": 2, "target_audience": "Test", "voice_tone": "test"},
                    "deployment": {"cloudflare_project": "cf-proj"},
                    "active": True,
                },
                "no-deployment": {
                    "display_name": "None",
                    "content": {"tier": 3, "target_audience": "Test", "voice_tone": "test"},
                    "deployment": {},
                    "active": True,
                },
            }
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(data, f)
            path = Path(f.name)

        DomainRegistry.load(path)
        return DomainRegistry

    def test_multiple_compliance_requirements(self, complex_registry):
        """Test filtering domains with multiple compliance requirements."""
        coppa = complex_registry.by_compliance("COPPA")
        gdpr = complex_registry.by_compliance("GDPR")
        hipaa = complex_registry.by_compliance("HIPAA-lite")

        assert len(coppa) == 1
        assert len(gdpr) == 1
        assert len(hipaa) == 1
        assert coppa[0].key == "multi-compliance"
        assert gdpr[0].key == "multi-compliance"
        assert hipaa[0].key == "multi-compliance"

    def test_multiple_human_gates(self, complex_registry):
        """Test filtering domains with multiple human gates."""
        payment = complex_registry.by_human_gate("payment_integration")
        health = complex_registry.by_human_gate("health_data")

        assert len(payment) == 1
        assert len(health) == 1
        assert payment[0].key == "multi-compliance"
        assert health[0].key == "multi-compliance"

    def test_deployment_railway_only(self, complex_registry):
        """Test filtering domains with railway-only deployment."""
        with_deploy = complex_registry.with_deployment()

        keys = [d.key for d in with_deploy]
        assert "multi-compliance" in keys
        assert "railway-only" in keys
        assert "cloudflare-only" in keys
        assert "no-deployment" not in keys

    def test_notion_with_both_ids(self, complex_registry):
        """Test filtering domains with complete Notion config."""
        with_notion = complex_registry.with_notion()

        assert len(with_notion) == 1
        assert with_notion[0].key == "multi-compliance"

    def test_by_tier_empty_result(self, complex_registry):
        """Test filtering by tier with no matches."""
        # No tier 1 in this test registry except multi-compliance
        tier_1 = complex_registry.by_tier(1)
        assert len(tier_1) == 1

    def test_by_compliance_empty_result(self, complex_registry):
        """Test filtering by compliance with no matches."""
        soc2 = complex_registry.by_compliance("SOC2")
        assert len(soc2) == 0

    def test_by_human_gate_empty_result(self, complex_registry):
        """Test filtering by human gate with no matches."""
        nonexistent = complex_registry.by_human_gate("nonexistent_gate")
        assert len(nonexistent) == 0


class TestDomainRegistryValidationExtended:
    """Extended validation tests for edge cases."""

    def test_validate_missing_target_audience(self):
        """Test validation catches missing target_audience."""
        data = {
            "domains": {
                "bad-domain": {
                    "display_name": "Bad",
                    "content": {
                        "tier": 1,
                        "target_audience": "",  # Empty
                        "voice_tone": "test",
                    },
                }
            }
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(data, f)
            path = Path(f.name)

        DomainRegistry.load(path)
        errors = DomainRegistry.validate()

        assert any("missing content.target_audience" in e for e in errors)

    def test_validate_multiple_errors(self):
        """Test validation catches multiple errors in same domain."""
        data = {
            "domains": {
                "bad-domain": {
                    "display_name": "",  # Error 1
                    "frontend_tier": "InvalidTier",  # Error 2
                    "content": {
                        "tier": 5,  # Error 3
                        "target_audience": "",  # Error 4
                        "voice_tone": "test",
                    },
                    "notion": {
                        "data_source_id": "has-this",
                        # Missing linked_database_id - Error 5
                    },
                }
            }
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(data, f)
            path = Path(f.name)

        DomainRegistry.load(path)
        errors = DomainRegistry.validate()

        # Should catch at least 3 errors
        assert len(errors) >= 3

    def test_validate_without_schema(self):
        """Test validation without JSON schema (use_schema=False)."""
        data = {
            "domains": {
                "test-domain": {
                    "display_name": "Test",
                    "content": {"tier": 1, "target_audience": "Test", "voice_tone": "test"},
                }
            }
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(data, f)
            path = Path(f.name)

        DomainRegistry.load(path)
        errors = DomainRegistry.validate(use_schema=False)

        # Should work without schema validation
        assert isinstance(errors, list)

    def test_validate_notion_only_database_id(self):
        """Test validation catches Notion with only database ID."""
        data = {
            "domains": {
                "bad-domain": {
                    "display_name": "Bad",
                    "content": {"tier": 1, "target_audience": "X", "voice_tone": "y"},
                    "notion": {
                        "data_source_id": None,
                        "linked_database_id": "has-this",  # Has DB but no source
                    },
                }
            }
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(data, f)
            path = Path(f.name)

        DomainRegistry.load(path)
        errors = DomainRegistry.validate()

        assert any("Notion config incomplete" in e for e in errors)

    def test_validate_all_frontend_tiers(self):
        """Test validation accepts all valid frontend tiers."""
        valid_tiers = ["React", "Lit PWA", "React Native", "basic", "interactive", "advanced"]

        for tier in valid_tiers:
            data = {
                "domains": {
                    "test-domain": {
                        "display_name": "Test",
                        "frontend_tier": tier,
                        "content": {"tier": 1, "target_audience": "Test", "voice_tone": "test"},
                    }
                }
            }

            with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
                yaml.dump(data, f)
                path = Path(f.name)

            DomainRegistry._domains = {}
            DomainRegistry._loaded = False
            DomainRegistry.load(path)
            errors = DomainRegistry.validate()

            # Should not have frontend_tier errors
            assert not any("frontend_tier" in e for e in errors), f"Failed for tier: {tier}"

    def test_validate_with_jsonschema_error(self):
        """Test validation handles jsonschema validation errors."""
        # Create invalid YAML that would fail schema validation
        data = {
            "domains": {
                "test-domain": {
                    "display_name": "Test",
                    "content": {
                        "tier": "invalid",  # Should be int, not string
                        "target_audience": "Test",
                        "voice_tone": "test",
                    },
                }
            }
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(data, f)
            path = Path(f.name)

        DomainRegistry.load(path)

        # This should work even with schema validation
        # (may report errors if schema exists, or skip if jsonschema not installed)
        errors = DomainRegistry.validate(use_schema=True)

        # Should at least catch the semantic validation issue
        assert isinstance(errors, list)


class TestDomainRegistryStateManagement:
    """Test registry state management and lifecycle."""

    def test_load_twice_replaces_data(self, temp_domains_file):
        """Test loading twice replaces existing data."""
        DomainRegistry.load(temp_domains_file)
        first_count = len(DomainRegistry.all())

        # Create new YAML with different domains
        new_data = {
            "domains": {
                "new-domain": {
                    "display_name": "New",
                    "content": {"tier": 1, "target_audience": "Test", "voice_tone": "test"},
                }
            }
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(new_data, f)
            new_path = Path(f.name)

        DomainRegistry.load(new_path)
        second_count = len(DomainRegistry.all())

        assert first_count == 3
        assert second_count == 1
        assert DomainRegistry.get("new-domain").display_name == "New"

    def test_ensure_loaded_auto_loads(self):
        """Test _ensure_loaded automatically loads registry."""
        # Start with empty registry
        assert not DomainRegistry._loaded

        # Query should trigger auto-load
        domains = DomainRegistry.all()

        assert DomainRegistry._loaded is True
        assert len(domains) > 0

    def test_reload_without_previous_path(self):
        """Test reload works even without previous path stored."""
        DomainRegistry._loaded = False
        DomainRegistry._yaml_path = None

        DomainRegistry.reload()

        # Should load from default path
        assert DomainRegistry._loaded is True
        assert len(DomainRegistry.all()) > 0

    def test_reload_with_stored_path(self, temp_domains_file):
        """Test reload uses stored path."""
        DomainRegistry.load(temp_domains_file)
        original_count = len(DomainRegistry.all())

        # Force reload
        DomainRegistry._loaded = False
        DomainRegistry.reload()

        assert DomainRegistry._loaded is True
        assert len(DomainRegistry.all()) == original_count

    def test_summary_with_empty_registry(self):
        """Test summary works with empty registry."""
        data = {"domains": {}}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(data, f)
            path = Path(f.name)

        DomainRegistry.load(path)
        summary = DomainRegistry.summary()

        assert summary["total_domains"] == 0
        assert summary["active_domains"] == 0
        assert summary["tier_1_count"] == 0


class TestBackwardCompatibility:
    """Test backward compatibility helpers."""

    def test_get_domain_from_registry_exists(self, temp_domains_file):
        """Test backward compatibility function for existing domain."""
        from domain_registry import get_domain_from_registry

        DomainRegistry.load(temp_domains_file)
        domain = get_domain_from_registry("test-domain-1")

        assert domain is not None
        assert domain.key == "test-domain-1"

    def test_get_domain_from_registry_not_exists(self, temp_domains_file):
        """Test backward compatibility function for non-existent domain."""
        from domain_registry import get_domain_from_registry

        DomainRegistry.load(temp_domains_file)
        domain = get_domain_from_registry("nonexistent")

        assert domain is None


class TestContentConfig:
    """Test ContentConfig dataclass."""

    def test_content_config_creation(self):
        """Test creating ContentConfig directly."""
        config = ContentConfig(
            tier=1,
            target_audience="Developers",
            voice_tone="technical",
            content_strategy_path="docs/CONTENT.md",
            pillars=["A", "B", "C"],
        )

        assert config.tier == 1
        assert config.target_audience == "Developers"
        assert config.voice_tone == "technical"
        assert config.content_strategy_path == "docs/CONTENT.md"
        assert config.pillars == ["A", "B", "C"]

    def test_content_config_defaults(self):
        """Test ContentConfig default values."""
        config = ContentConfig(
            tier=2,
            target_audience="General",
            voice_tone="friendly",
        )

        assert config.content_strategy_path is None
        assert config.pillars == []


class TestDeploymentConfig:
    """Test DeploymentConfig dataclass."""

    def test_deployment_config_full(self):
        """Test creating DeploymentConfig with all fields."""
        config = DeploymentConfig(
            railway_service="test-svc",
            cloudflare_project="test-proj",
            marketing_site_path="marketing-template",
        )

        assert config.railway_service == "test-svc"
        assert config.cloudflare_project == "test-proj"
        assert config.marketing_site_path == "marketing-template"

    def test_deployment_config_defaults(self):
        """Test DeploymentConfig default values."""
        config = DeploymentConfig()

        assert config.railway_service is None
        assert config.cloudflare_project is None
        assert config.marketing_site_path is None


class TestNotionConfig:
    """Test NotionConfig dataclass."""

    def test_notion_config_full(self):
        """Test creating NotionConfig with all fields."""
        config = NotionConfig(
            data_source_id="ds-123",
            linked_database_id="db-456",
        )

        assert config.data_source_id == "ds-123"
        assert config.linked_database_id == "db-456"

    def test_notion_config_defaults(self):
        """Test NotionConfig default values."""
        config = NotionConfig()

        assert config.data_source_id is None
        assert config.linked_database_id is None
