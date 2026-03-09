"""Tests for forge config CLI commands.

Covers forge_harness/cli_v2/config.py:
    - forge config llm-show
    - forge config llm-set --provider PROVIDER --model MODEL [--temperature T] [--max-tokens N]

Strategy:
    - CliRunner.invoke for all integration paths.
    - CommandCenterClient._request is mocked to return pre-built APIResponse
      objects so no real HTTP is attempted.
    - JSON envelope shape is validated for --json output paths.
    - Validation-error branches are exercised (no fields, bad temperature, etc.).
    - Service-unavailable branches are exercised.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import yaml
from click.testing import CliRunner
from forge_harness.cli_v2 import cli

from forge_harness.command_center_client import APIResponse
from forge_harness.task_manager.feature_flags import FeatureFlags

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def llm_config_response():
    """Sample GET /api/config/llm response payload."""
    return {
        "llm": {
            "provider": "claude",
            "model": "claude-sonnet-4-5",
            "temperature": 0.7,
            "max_tokens": 4096,
        },
        "timestamp": "2026-02-23T12:00:00+00:00",
    }


@pytest.fixture
def llm_update_response():
    """Sample POST /api/config/llm response payload after update."""
    return {
        "status": "updated",
        "llm": {
            "provider": "claude",
            "model": "claude-opus-4-6",
            "temperature": 0.5,
            "max_tokens": 8192,
        },
        "timestamp": "2026-02-23T12:01:00+00:00",
    }


def _make_response(data, success=True):
    """Build an APIResponse from a data payload."""
    return APIResponse(success=success, data=data, error=None)


# ---------------------------------------------------------------------------
# forge config llm-show
# ---------------------------------------------------------------------------


class TestConfigLlmShow:
    """Tests for `forge config llm-show`."""

    def test_llm_show_renders_details(self, runner, llm_config_response):
        """llm-show displays provider, model, temperature, max_tokens."""
        mock_resp = _make_response(llm_config_response)
        with patch(
            "forge_harness.command_center_client.CommandCenterClient._request",
            AsyncMock(return_value=mock_resp),
        ):
            result = runner.invoke(cli, ["config", "llm-show"])

        assert result.exit_code == 0, result.output
        assert "claude" in result.output
        assert "claude-sonnet-4-5" in result.output
        assert "0.7" in result.output
        assert "4096" in result.output

    def test_llm_show_json_output(self, runner, llm_config_response):
        """llm-show --json returns a valid FORGE envelope."""
        mock_resp = _make_response(llm_config_response)
        with patch(
            "forge_harness.command_center_client.CommandCenterClient._request",
            AsyncMock(return_value=mock_resp),
        ):
            result = runner.invoke(cli, ["config", "--json", "llm-show"])

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["success"] is True
        assert payload["command"] == "forge config llm-show"
        assert "data" in payload
        llm = payload["data"]["llm"]
        assert llm["provider"] == "claude"
        assert llm["model"] == "claude-sonnet-4-5"

    def test_llm_show_service_unavailable(self, runner):
        """llm-show exits with code 4 when API is unreachable."""
        mock_resp = APIResponse(success=False, data=None, error="connection refused")
        with patch(
            "forge_harness.command_center_client.CommandCenterClient._request",
            AsyncMock(return_value=mock_resp),
        ):
            result = runner.invoke(cli, ["config", "llm-show"])

        assert result.exit_code == 4

    def test_llm_show_service_unavailable_json(self, runner):
        """llm-show --json emits error envelope when service is unavailable."""
        mock_resp = APIResponse(success=False, data=None, error="timeout")
        with patch(
            "forge_harness.command_center_client.CommandCenterClient._request",
            AsyncMock(return_value=mock_resp),
        ):
            result = runner.invoke(cli, ["config", "--json", "llm-show"])

        assert result.exit_code == 4
        payload = json.loads(result.output)
        assert payload["success"] is False

    def test_llm_show_missing_optional_fields(self, runner):
        """llm-show handles a response missing temperature/max_tokens."""
        data = {
            "llm": {"provider": "openai", "model": "gpt-4o"},
            "timestamp": "2026-02-23T12:00:00+00:00",
        }
        mock_resp = _make_response(data)
        with patch(
            "forge_harness.command_center_client.CommandCenterClient._request",
            AsyncMock(return_value=mock_resp),
        ):
            result = runner.invoke(cli, ["config", "llm-show"])

        assert result.exit_code == 0, result.output
        assert "openai" in result.output
        assert "gpt-4o" in result.output

    def test_llm_show_timestamp_displayed(self, runner, llm_config_response):
        """llm-show renders the fetched-at timestamp in human mode."""
        mock_resp = _make_response(llm_config_response)
        with patch(
            "forge_harness.command_center_client.CommandCenterClient._request",
            AsyncMock(return_value=mock_resp),
        ):
            result = runner.invoke(cli, ["config", "llm-show"])

        assert result.exit_code == 0, result.output
        assert "2026-02-23" in result.output


# ---------------------------------------------------------------------------
# forge config llm-set
# ---------------------------------------------------------------------------


class TestConfigLlmSet:
    """Tests for `forge config llm-set`."""

    def test_llm_set_provider_and_model(self, runner, llm_update_response):
        """llm-set with --provider and --model succeeds and shows updated config."""
        mock_resp = _make_response(llm_update_response)
        with patch(
            "forge_harness.command_center_client.CommandCenterClient._request",
            AsyncMock(return_value=mock_resp),
        ):
            result = runner.invoke(
                cli,
                ["config", "llm-set", "--provider", "claude", "--model", "claude-opus-4-6"],
            )

        assert result.exit_code == 0, result.output
        assert "updated" in result.output.lower() or "claude-opus-4-6" in result.output

    def test_llm_set_json_output(self, runner, llm_update_response):
        """llm-set --json returns a valid FORGE envelope with updated config."""
        mock_resp = _make_response(llm_update_response)
        with patch(
            "forge_harness.command_center_client.CommandCenterClient._request",
            AsyncMock(return_value=mock_resp),
        ):
            result = runner.invoke(
                cli,
                ["config", "--json", "llm-set", "--model", "claude-opus-4-6"],
            )

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["success"] is True
        assert payload["command"] == "forge config llm-set"
        assert payload["data"]["llm"]["model"] == "claude-opus-4-6"

    def test_llm_set_temperature_only(self, runner, llm_update_response):
        """llm-set accepts --temperature alone."""
        mock_resp = _make_response(llm_update_response)
        with patch(
            "forge_harness.command_center_client.CommandCenterClient._request",
            AsyncMock(return_value=mock_resp),
        ):
            result = runner.invoke(cli, ["config", "llm-set", "--temperature", "0.3"])

        assert result.exit_code == 0, result.output

    def test_llm_set_max_tokens_only(self, runner, llm_update_response):
        """llm-set accepts --max-tokens alone."""
        mock_resp = _make_response(llm_update_response)
        with patch(
            "forge_harness.command_center_client.CommandCenterClient._request",
            AsyncMock(return_value=mock_resp),
        ):
            result = runner.invoke(cli, ["config", "llm-set", "--max-tokens", "8192"])

        assert result.exit_code == 0, result.output

    def test_llm_set_all_fields(self, runner, llm_update_response):
        """llm-set accepts all four fields at once."""
        captured: list[dict] = []

        async def _fake_request(self, method, path, data=None, params=None):
            if data:
                captured.append(data)
            return _make_response(llm_update_response)

        with patch(
            "forge_harness.command_center_client.CommandCenterClient._request",
            _fake_request,
        ):
            result = runner.invoke(
                cli,
                [
                    "config",
                    "llm-set",
                    "--provider",
                    "claude",
                    "--model",
                    "claude-opus-4-6",
                    "--temperature",
                    "0.5",
                    "--max-tokens",
                    "8192",
                ],
            )

        assert result.exit_code == 0, result.output
        assert len(captured) == 1
        body = captured[0]
        assert body["provider"] == "claude"
        assert body["model"] == "claude-opus-4-6"
        assert body["temperature"] == 0.5
        assert body["max_tokens"] == 8192


# ---------------------------------------------------------------------------
# forge config get/set - YAML-backed feature flags
# ---------------------------------------------------------------------------


class TestConfigFeatureFlags:
    """Tests for `forge config get/set` feature flag helpers."""

    def test_feature_set_node_override_and_get_json(self, runner, tmp_path: Path) -> None:
        """set with --node writes node_overrides and get --json reflects it."""
        with runner.isolated_filesystem():
            config_dir = Path(".forge") / "config"
            config_dir.mkdir(parents=True)
            features_path = config_dir / "features.yaml"
            features_path.write_text(
                "features:\n"
                "  v3_orchestrator_enabled: true\n",
                encoding="utf-8",
            )

            # Disable orchestrator for a specific node
            result_set = runner.invoke(
                cli,
                [
                    "config",
                    "set",
                    "v3_orchestrator_enabled",
                    "false",
                    "--node",
                    "prya",
                ],
            )
            assert result_set.exit_code == 0, result_set.output

            # JSON get should reflect the node override
            result_get = runner.invoke(
                cli,
                [
                    "config",
                    "--json",
                    "get",
                    "v3_orchestrator_enabled",
                    "--node",
                    "prya",
                ],
            )
            assert result_get.exit_code == 0, result_get.output
            payload = json.loads(result_get.output)
            assert payload["success"] is True
            assert payload["command"] == "forge config get"
            assert payload["data"]["flag"] == "v3_orchestrator_enabled"
            assert payload["data"]["node"] == "prya"
            assert payload["data"]["enabled"] is False

            # YAML on disk should contain the node_overrides entry
            updated = yaml.safe_load(features_path.read_text(encoding="utf-8"))
            assert updated["node_overrides"]["prya"]["v3_orchestrator_enabled"] is False

    def test_feature_set_global_override(self, runner, tmp_path: Path) -> None:
        """set --global updates the global features mapping."""
        with runner.isolated_filesystem():
            config_dir = Path(".forge") / "config"
            config_dir.mkdir(parents=True)
            features_path = config_dir / "features.yaml"
            features_path.write_text(
                "features:\n"
                "  v3_orchestrator_enabled: true\n",
                encoding="utf-8",
            )

            result = runner.invoke(
                cli,
                [
                    "config",
                    "set",
                    "v3_orchestrator_enabled",
                    "false",
                    "--global",
                ],
            )
            assert result.exit_code == 0, result.output

            updated = yaml.safe_load(features_path.read_text(encoding="utf-8"))
            assert updated["features"]["v3_orchestrator_enabled"] is False

    def test_llm_set_no_fields_exits_9(self, runner):
        """llm-set with no options exits with code 9 (validation error)."""
        result = runner.invoke(cli, ["config", "llm-set"])
        assert result.exit_code == 9

    def test_llm_set_no_fields_json_error(self, runner):
        """llm-set --json with no options emits error envelope (exit 9)."""
        result = runner.invoke(cli, ["config", "--json", "llm-set"])
        assert result.exit_code == 9
        payload = json.loads(result.output)
        assert payload["success"] is False

    def test_llm_set_temperature_too_high_exits_9(self, runner):
        """llm-set rejects --temperature > 1.0 with exit code 9."""
        result = runner.invoke(cli, ["config", "llm-set", "--temperature", "1.5"])
        assert result.exit_code == 9

    def test_llm_set_temperature_negative_exits_9(self, runner):
        """llm-set rejects negative --temperature with exit code 9."""
        result = runner.invoke(cli, ["config", "llm-set", "--temperature", "-0.1"])
        assert result.exit_code == 9

    def test_llm_set_temperature_boundary_zero(self, runner, llm_update_response):
        """llm-set accepts --temperature 0.0 (lower boundary)."""
        mock_resp = _make_response(llm_update_response)
        with patch(
            "forge_harness.command_center_client.CommandCenterClient._request",
            AsyncMock(return_value=mock_resp),
        ):
            result = runner.invoke(cli, ["config", "llm-set", "--temperature", "0.0"])

        assert result.exit_code == 0, result.output

    def test_llm_set_temperature_boundary_one(self, runner, llm_update_response):
        """llm-set accepts --temperature 1.0 (upper boundary)."""
        mock_resp = _make_response(llm_update_response)
        with patch(
            "forge_harness.command_center_client.CommandCenterClient._request",
            AsyncMock(return_value=mock_resp),
        ):
            result = runner.invoke(cli, ["config", "llm-set", "--temperature", "1.0"])

        assert result.exit_code == 0, result.output

    def test_llm_set_max_tokens_zero_exits_9(self, runner):
        """llm-set rejects --max-tokens 0 with exit code 9."""
        result = runner.invoke(cli, ["config", "llm-set", "--max-tokens", "0"])
        assert result.exit_code == 9

    def test_llm_set_max_tokens_negative_exits_9(self, runner):
        """llm-set rejects negative --max-tokens with exit code 9."""
        result = runner.invoke(cli, ["config", "llm-set", "--max-tokens", "-1"])
        assert result.exit_code == 9

    def test_llm_set_service_unavailable(self, runner):
        """llm-set exits with code 4 when the config API is unreachable."""
        mock_resp = APIResponse(success=False, data=None, error="connection refused")
        with patch(
            "forge_harness.command_center_client.CommandCenterClient._request",
            AsyncMock(return_value=mock_resp),
        ):
            result = runner.invoke(cli, ["config", "llm-set", "--model", "gpt-4"])

        assert result.exit_code == 4

    def test_llm_set_service_unavailable_json(self, runner):
        """llm-set --json emits error envelope when service is unavailable."""
        mock_resp = APIResponse(success=False, data=None, error="timeout")
        with patch(
            "forge_harness.command_center_client.CommandCenterClient._request",
            AsyncMock(return_value=mock_resp),
        ):
            result = runner.invoke(cli, ["config", "--json", "llm-set", "--model", "gpt-4"])

        assert result.exit_code == 4
        payload = json.loads(result.output)
        assert payload["success"] is False

    def test_llm_set_payload_excludes_none_fields(self, runner, llm_update_response):
        """llm-set only sends fields that were explicitly provided."""
        captured: list[dict] = []

        async def _fake_request(self, method, path, data=None, params=None):
            if data:
                captured.append(data)
            return _make_response(llm_update_response)

        with patch(
            "forge_harness.command_center_client.CommandCenterClient._request",
            _fake_request,
        ):
            result = runner.invoke(cli, ["config", "llm-set", "--model", "claude-opus-4-6"])

        assert result.exit_code == 0, result.output
        assert len(captured) == 1
        body = captured[0]
        assert "model" in body
        # provider, temperature, max_tokens should NOT be sent
        assert "provider" not in body
        assert "temperature" not in body
        assert "max_tokens" not in body

    def test_llm_set_short_flags(self, runner, llm_update_response):
        """llm-set short flags -p and -m work as aliases."""
        mock_resp = _make_response(llm_update_response)
        with patch(
            "forge_harness.command_center_client.CommandCenterClient._request",
            AsyncMock(return_value=mock_resp),
        ):
            result = runner.invoke(
                cli, ["config", "llm-set", "-p", "claude", "-m", "claude-opus-4-6"]
            )

        assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# forge config rc-show
# ---------------------------------------------------------------------------


class TestConfigRcShow:
    """Tests for `forge config rc-show`."""

    def test_rc_show_no_rc_files(self, runner, tmp_path):
        """rc-show reports when no rc file is present."""
        result = runner.invoke(cli, ["config", "rc-show"], env={"HOME": str(tmp_path)})

        assert result.exit_code == 0, result.output
        assert "none (no ~/.forgerc or ~/.forcerc found)" in result.output

    def test_rc_show_loads_forgerc_human_output(self, runner, tmp_path):
        """rc-show loads ~/.forgerc and redacts sensitive values."""
        rc_file = tmp_path / ".forgerc"
        rc_file.write_text(
            "\n".join(
                [
                    "FORGE_WEBHOOK_TOKEN=tokentest123456",
                    "COMMAND_CENTER_URL=http://localhost:8080",
                ]
            ),
            encoding="utf-8",
        )

        result = runner.invoke(cli, ["config", "rc-show"], env={"HOME": str(tmp_path)})

        assert result.exit_code == 0, result.output
        assert "Loaded from:" in result.output
        assert ".forgerc" in result.output
        assert "FORGE_WEBHOOK_TOKEN=toke...3456" in result.output
        assert "COMMAND_CENTER_URL=http://localhost:8080" in result.output

    def test_rc_show_json_output(self, runner, tmp_path):
        """rc-show --json emits rc metadata with loaded keys."""
        rc_file = tmp_path / ".forgerc"
        rc_file.write_text("FORGE_TEST_FLAG=1\n", encoding="utf-8")

        result = runner.invoke(cli, ["config", "--json", "rc-show"], env={"HOME": str(tmp_path)})

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["success"] is True
        assert payload["command"] == "forge config rc-show"
        data = payload["data"]
        assert data["loaded_from"] == str(rc_file)
        assert data["loaded_count"] == 1
        assert data["loaded_keys"] == ["FORGE_TEST_FLAG"]
        assert data["loaded"]["FORGE_TEST_FLAG"] == "1"

    def test_rc_show_respects_forge_rc_file_override(self, runner, tmp_path):
        """FORGE_RC_FILE takes precedence over ~/.forgerc and ~/.forcerc."""
        (tmp_path / ".forgerc").write_text("FORGE_TEST_FLAG=from-home\n", encoding="utf-8")
        custom_rc = tmp_path / "custom.rc"
        custom_rc.write_text("FORGE_TEST_FLAG=from-custom\n", encoding="utf-8")

        env = {
            "HOME": str(tmp_path),
            "FORGE_RC_FILE": str(custom_rc),
        }
        result = runner.invoke(cli, ["config", "--json", "rc-show"], env=env)

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        data = payload["data"]
        assert data["loaded_from"] == str(custom_rc)
        assert data["loaded"]["FORGE_TEST_FLAG"] == "from-custom"

    def test_rc_show_reports_api_url_alias(self, runner, tmp_path):
        """rc-show surfaces URL compatibility alias when only COMMAND_CENTER_URL exists."""
        rc_file = tmp_path / ".forgerc"
        rc_file.write_text("COMMAND_CENTER_URL=http://127.0.0.1:8080\n", encoding="utf-8")

        result = runner.invoke(cli, ["config", "--json", "rc-show"], env={"HOME": str(tmp_path)})

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        aliases = payload["data"]["aliases"]
        assert aliases["FORGE_API_URL"] == "COMMAND_CENTER_URL"


# ---------------------------------------------------------------------------
# forge config rc-init
# ---------------------------------------------------------------------------


class TestConfigRcInit:
    """Tests for `forge config rc-init`."""

    def test_rc_init_creates_file(self, runner, tmp_path):
        """rc-init writes a default rc file with canonical keys."""
        rc_path = tmp_path / ".forgerc"
        result = runner.invoke(
            cli,
            [
                "config",
                "rc-init",
                "--path",
                str(rc_path),
                "--api-url",
                "http://100.70.79.45:8080",
                "--api-token",
                "token-123",
                "--node-id",
                "nova",
            ],
        )

        assert result.exit_code == 0, result.output
        assert rc_path.exists()
        text = rc_path.read_text(encoding="utf-8")
        assert "FORGE_API_URL=http://100.70.79.45:8080" in text
        assert "COMMAND_CENTER_URL=http://100.70.79.45:8080" in text
        assert "FORGE_WEBHOOK_TOKEN=token-123" in text
        assert "FORGE_NODE_ID=nova" in text

    def test_rc_init_refuses_overwrite_without_flag(self, runner, tmp_path):
        """rc-init fails with validation code when file exists and --overwrite is missing."""
        rc_path = tmp_path / ".forgerc"
        rc_path.write_text("FORGE_API_URL=http://localhost:8080\n", encoding="utf-8")

        result = runner.invoke(cli, ["config", "rc-init", "--path", str(rc_path)])

        assert result.exit_code == 9
        assert "Refusing to overwrite" in result.output

    def test_rc_init_json_output(self, runner, tmp_path):
        """rc-init --json returns metadata envelope."""
        rc_path = tmp_path / ".forgerc"
        result = runner.invoke(
            cli,
            ["config", "--json", "rc-init", "--path", str(rc_path), "--api-url", "http://localhost:8080"],
        )

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["success"] is True
        assert payload["command"] == "forge config rc-init"
        assert payload["data"]["path"] == str(rc_path)
        assert payload["data"]["api_url"] == "http://localhost:8080"


# ---------------------------------------------------------------------------
# Help text sanity checks
# ---------------------------------------------------------------------------


class TestConfigHelp:
    """Verify --help output is present for all config subcommands."""

    def test_config_group_help(self, runner):
        result = runner.invoke(cli, ["config", "--help"])
        assert result.exit_code == 0
        assert "llm-show" in result.output
        assert "llm-set" in result.output
        assert "rc-show" in result.output
        assert "rc-init" in result.output

    def test_llm_show_help(self, runner):
        result = runner.invoke(cli, ["config", "llm-show", "--help"])
        assert result.exit_code == 0

    def test_llm_set_help(self, runner):
        result = runner.invoke(cli, ["config", "llm-set", "--help"])
        assert result.exit_code == 0
        assert "--provider" in result.output
        assert "--model" in result.output
        assert "--temperature" in result.output
        assert "--max-tokens" in result.output

    def test_rc_show_help(self, runner):
        result = runner.invoke(cli, ["config", "rc-show", "--help"])
        assert result.exit_code == 0
        assert "--home" in result.output

    def test_rc_init_help(self, runner):
        result = runner.invoke(cli, ["config", "rc-init", "--help"])
        assert result.exit_code == 0
        assert "--path" in result.output
        assert "--api-url" in result.output
        assert "--api-token" in result.output
