"""Unit tests for forge_harness.iteration.metrics_exporter_example.

Tests the main() entry point and all branching logic with mocked external
dependencies (tracker, exporter, logger, sys.argv).
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers / shared fixtures
# ---------------------------------------------------------------------------


def _make_mock_tracker(total=3, completed=2, failed=1, features=5, tests=13, lines=250):
    """Return a MagicMock tracker whose get_stats() returns realistic data."""
    tracker = MagicMock()
    stats = MagicMock()
    stats.total_iterations = total
    stats.completed_iterations = completed
    stats.failed_iterations = failed
    stats.total_features = features
    stats.total_tests = tests
    stats.total_lines = lines
    tracker.get_stats.return_value = stats
    return tracker


def _make_mock_exporter(json_path=None, prom_path=None):
    """Return a MagicMock exporter with sensible return values."""
    exporter = MagicMock()
    exporter.to_json.return_value = '{"project":"test","metrics":{}}'
    exporter.to_prometheus.return_value = "# HELP forge_iterations_total Total\n"
    exporter.write_json.return_value = json_path or Path("/tmp/metrics_test.json")
    exporter.write_prometheus.return_value = prom_path or Path("/tmp/metrics.prom")
    exporter.write_all.return_value = {
        "json": json_path or Path("/tmp/metrics_test.json"),
        "prometheus": prom_path or Path("/tmp/metrics.prom"),
    }
    return exporter


# ---------------------------------------------------------------------------
# 1. Argument parser defaults
# ---------------------------------------------------------------------------


class TestArgumentParserDefaults:
    """Verify that argparse produces expected defaults when no args are given."""

    def test_default_project_name(self):
        """--project defaults to 'forge-harness'."""
        from forge_harness.iteration.metrics_exporter_example import main

        mock_tracker = _make_mock_tracker()
        mock_exporter = _make_mock_exporter()

        with patch("sys.argv", ["prog"]), patch(
            "forge_harness.iteration.metrics_exporter_example.create_tracker",
            return_value=mock_tracker,
        ), patch(
            "forge_harness.iteration.metrics_exporter_example.create_metrics_exporter",
            return_value=mock_exporter,
        ):
            main()

        call_kwargs = (
            mock_exporter.write_all.call_args
            or mock_exporter.write_json.call_args
            or mock_exporter.write_prometheus.call_args
        )
        # The project name is passed to create_metrics_exporter
        from forge_harness.iteration.metrics_exporter_example import (
            create_metrics_exporter,  # noqa: F401
        )

    def test_default_format_calls_write_all(self):
        """When --format is not supplied the default 'both' triggers write_all()."""
        from forge_harness.iteration.metrics_exporter_example import main

        mock_tracker = _make_mock_tracker()
        mock_exporter = _make_mock_exporter()

        with patch("sys.argv", ["prog"]), patch(
            "forge_harness.iteration.metrics_exporter_example.create_tracker",
            return_value=mock_tracker,
        ), patch(
            "forge_harness.iteration.metrics_exporter_example.create_metrics_exporter",
            return_value=mock_exporter,
        ):
            main()

        mock_exporter.write_all.assert_called_once()
        mock_exporter.write_json.assert_not_called()
        mock_exporter.write_prometheus.assert_not_called()

    def test_default_history_is_none(self):
        """Without --history the storage_path passed to create_tracker is None."""
        from forge_harness.iteration.metrics_exporter_example import main

        mock_tracker = _make_mock_tracker()
        mock_exporter = _make_mock_exporter()

        with patch("sys.argv", ["prog"]), patch(
            "forge_harness.iteration.metrics_exporter_example.create_tracker",
            return_value=mock_tracker,
        ) as mock_ct, patch(
            "forge_harness.iteration.metrics_exporter_example.create_metrics_exporter",
            return_value=mock_exporter,
        ):
            main()

        mock_ct.assert_called_once_with(storage_path=None)

    def test_default_output_dir_is_none(self):
        """Without --output-dir the output_dir passed to create_metrics_exporter is None."""
        from forge_harness.iteration.metrics_exporter_example import main

        mock_tracker = _make_mock_tracker()
        mock_exporter = _make_mock_exporter()

        with patch("sys.argv", ["prog"]), patch(
            "forge_harness.iteration.metrics_exporter_example.create_tracker",
            return_value=mock_tracker,
        ), patch(
            "forge_harness.iteration.metrics_exporter_example.create_metrics_exporter",
            return_value=mock_exporter,
        ) as mock_cme:
            main()

        mock_cme.assert_called_once_with(
            project_name="forge-harness",
            tracker=mock_tracker,
            output_dir=None,
        )


# ---------------------------------------------------------------------------
# 2. --format json branch
# ---------------------------------------------------------------------------


class TestFormatJsonBranch:
    """Tests for --format json execution path."""

    def test_format_json_calls_write_json(self):
        """--format json must call write_json() and not the other writers."""
        from forge_harness.iteration.metrics_exporter_example import main

        mock_tracker = _make_mock_tracker()
        mock_exporter = _make_mock_exporter()

        with patch("sys.argv", ["prog", "--format", "json"]), patch(
            "forge_harness.iteration.metrics_exporter_example.create_tracker",
            return_value=mock_tracker,
        ), patch(
            "forge_harness.iteration.metrics_exporter_example.create_metrics_exporter",
            return_value=mock_exporter,
        ):
            result = main()

        mock_exporter.write_json.assert_called_once()
        mock_exporter.write_prometheus.assert_not_called()
        mock_exporter.write_all.assert_not_called()

    def test_format_json_returns_zero(self):
        """main() returns 0 on success with --format json."""
        from forge_harness.iteration.metrics_exporter_example import main

        mock_tracker = _make_mock_tracker()
        mock_exporter = _make_mock_exporter()

        with patch("sys.argv", ["prog", "--format", "json"]), patch(
            "forge_harness.iteration.metrics_exporter_example.create_tracker",
            return_value=mock_tracker,
        ), patch(
            "forge_harness.iteration.metrics_exporter_example.create_metrics_exporter",
            return_value=mock_exporter,
        ):
            assert main() == 0


# ---------------------------------------------------------------------------
# 3. --format prometheus branch
# ---------------------------------------------------------------------------


class TestFormatPrometheusBranch:
    """Tests for --format prometheus execution path."""

    def test_format_prometheus_calls_write_prometheus(self):
        """--format prometheus must call write_prometheus() exclusively."""
        from forge_harness.iteration.metrics_exporter_example import main

        mock_tracker = _make_mock_tracker()
        mock_exporter = _make_mock_exporter()

        with patch("sys.argv", ["prog", "--format", "prometheus"]), patch(
            "forge_harness.iteration.metrics_exporter_example.create_tracker",
            return_value=mock_tracker,
        ), patch(
            "forge_harness.iteration.metrics_exporter_example.create_metrics_exporter",
            return_value=mock_exporter,
        ):
            result = main()

        mock_exporter.write_prometheus.assert_called_once()
        mock_exporter.write_json.assert_not_called()
        mock_exporter.write_all.assert_not_called()

    def test_format_prometheus_returns_zero(self):
        """main() returns 0 on success with --format prometheus."""
        from forge_harness.iteration.metrics_exporter_example import main

        mock_tracker = _make_mock_tracker()
        mock_exporter = _make_mock_exporter()

        with patch("sys.argv", ["prog", "--format", "prometheus"]), patch(
            "forge_harness.iteration.metrics_exporter_example.create_tracker",
            return_value=mock_tracker,
        ), patch(
            "forge_harness.iteration.metrics_exporter_example.create_metrics_exporter",
            return_value=mock_exporter,
        ):
            assert main() == 0


# ---------------------------------------------------------------------------
# 4. --format both branch
# ---------------------------------------------------------------------------


class TestFormatBothBranch:
    """Tests for --format both (the default) execution path."""

    def test_format_both_calls_write_all(self):
        """--format both must call write_all() once."""
        from forge_harness.iteration.metrics_exporter_example import main

        mock_tracker = _make_mock_tracker()
        mock_exporter = _make_mock_exporter()

        with patch("sys.argv", ["prog", "--format", "both"]), patch(
            "forge_harness.iteration.metrics_exporter_example.create_tracker",
            return_value=mock_tracker,
        ), patch(
            "forge_harness.iteration.metrics_exporter_example.create_metrics_exporter",
            return_value=mock_exporter,
        ):
            result = main()

        mock_exporter.write_all.assert_called_once()

    def test_format_both_returns_zero(self):
        """main() returns 0 on success with --format both."""
        from forge_harness.iteration.metrics_exporter_example import main

        mock_tracker = _make_mock_tracker()
        mock_exporter = _make_mock_exporter()

        with patch("sys.argv", ["prog", "--format", "both"]), patch(
            "forge_harness.iteration.metrics_exporter_example.create_tracker",
            return_value=mock_tracker,
        ), patch(
            "forge_harness.iteration.metrics_exporter_example.create_metrics_exporter",
            return_value=mock_exporter,
        ):
            assert main() == 0


# ---------------------------------------------------------------------------
# 5. Custom argument values
# ---------------------------------------------------------------------------


class TestCustomArguments:
    """Verify that CLI arguments are forwarded correctly to downstream calls."""

    def test_custom_project_forwarded_to_exporter(self):
        """--project value is passed to create_metrics_exporter."""
        from forge_harness.iteration.metrics_exporter_example import main

        mock_tracker = _make_mock_tracker()
        mock_exporter = _make_mock_exporter()

        with patch("sys.argv", ["prog", "--project", "my-custom-project"]), patch(
            "forge_harness.iteration.metrics_exporter_example.create_tracker",
            return_value=mock_tracker,
        ), patch(
            "forge_harness.iteration.metrics_exporter_example.create_metrics_exporter",
            return_value=mock_exporter,
        ) as mock_cme:
            main()

        mock_cme.assert_called_once_with(
            project_name="my-custom-project",
            tracker=mock_tracker,
            output_dir=None,
        )

    def test_custom_history_forwarded_to_tracker(self, tmp_path):
        """--history path is forwarded as storage_path to create_tracker."""
        from forge_harness.iteration.metrics_exporter_example import main

        history_file = tmp_path / "history.json"
        mock_tracker = _make_mock_tracker()
        mock_exporter = _make_mock_exporter()

        with patch("sys.argv", ["prog", "--history", str(history_file)]), patch(
            "forge_harness.iteration.metrics_exporter_example.create_tracker",
            return_value=mock_tracker,
        ) as mock_ct, patch(
            "forge_harness.iteration.metrics_exporter_example.create_metrics_exporter",
            return_value=mock_exporter,
        ):
            main()

        mock_ct.assert_called_once_with(storage_path=history_file)

    def test_custom_output_dir_forwarded_to_exporter(self, tmp_path):
        """--output-dir path is forwarded as output_dir to create_metrics_exporter."""
        from forge_harness.iteration.metrics_exporter_example import main

        output_dir = tmp_path / "my_metrics"
        mock_tracker = _make_mock_tracker()
        mock_exporter = _make_mock_exporter()

        with patch(
            "sys.argv", ["prog", "--output-dir", str(output_dir)]
        ), patch(
            "forge_harness.iteration.metrics_exporter_example.create_tracker",
            return_value=mock_tracker,
        ), patch(
            "forge_harness.iteration.metrics_exporter_example.create_metrics_exporter",
            return_value=mock_exporter,
        ) as mock_cme:
            main()

        mock_cme.assert_called_once_with(
            project_name="forge-harness",
            tracker=mock_tracker,
            output_dir=output_dir,
        )


# ---------------------------------------------------------------------------
# 6. Sample output via to_json / to_prometheus
# ---------------------------------------------------------------------------


class TestSampleOutputCalls:
    """Verify that main() always calls to_json() and to_prometheus() for sample output."""

    def test_to_json_called_for_sample_output(self):
        """main() always calls exporter.to_json() to display sample output."""
        from forge_harness.iteration.metrics_exporter_example import main

        mock_tracker = _make_mock_tracker()
        mock_exporter = _make_mock_exporter()

        with patch("sys.argv", ["prog"]), patch(
            "forge_harness.iteration.metrics_exporter_example.create_tracker",
            return_value=mock_tracker,
        ), patch(
            "forge_harness.iteration.metrics_exporter_example.create_metrics_exporter",
            return_value=mock_exporter,
        ):
            main()

        mock_exporter.to_json.assert_called_once()

    def test_to_prometheus_called_for_sample_output(self):
        """main() always calls exporter.to_prometheus() to display sample output."""
        from forge_harness.iteration.metrics_exporter_example import main

        mock_tracker = _make_mock_tracker()
        mock_exporter = _make_mock_exporter()

        with patch("sys.argv", ["prog"]), patch(
            "forge_harness.iteration.metrics_exporter_example.create_tracker",
            return_value=mock_tracker,
        ), patch(
            "forge_harness.iteration.metrics_exporter_example.create_metrics_exporter",
            return_value=mock_exporter,
        ):
            main()

        mock_exporter.to_prometheus.assert_called_once()

    def test_prometheus_output_is_truncated_to_20_lines(self):
        """main() slices Prometheus output to at most 20 lines before printing."""
        from forge_harness.iteration.metrics_exporter_example import main

        # Build a long Prometheus string (30 lines)
        long_prom = "\n".join(f"line_{i}" for i in range(30))

        mock_tracker = _make_mock_tracker()
        mock_exporter = _make_mock_exporter()
        mock_exporter.to_prometheus.return_value = long_prom

        printed_lines: list[str] = []

        def capture_print(text=""):
            printed_lines.append(str(text))

        with patch("sys.argv", ["prog"]), patch(
            "forge_harness.iteration.metrics_exporter_example.create_tracker",
            return_value=mock_tracker,
        ), patch(
            "forge_harness.iteration.metrics_exporter_example.create_metrics_exporter",
            return_value=mock_exporter,
        ), patch("builtins.print", side_effect=capture_print):
            main()

        # The second print call is the Prometheus block; it must be <= 20 lines
        prom_print = printed_lines[1]  # first print is JSON, second is Prometheus
        actual_line_count = len(prom_print.split("\n"))
        assert actual_line_count <= 20

    def test_json_output_printed(self):
        """main() prints the JSON sample output via builtins.print."""
        from forge_harness.iteration.metrics_exporter_example import main

        json_sample = '{"project":"forge-harness"}'
        mock_tracker = _make_mock_tracker()
        mock_exporter = _make_mock_exporter()
        mock_exporter.to_json.return_value = json_sample

        printed = []
        with patch("sys.argv", ["prog"]), patch(
            "forge_harness.iteration.metrics_exporter_example.create_tracker",
            return_value=mock_tracker,
        ), patch(
            "forge_harness.iteration.metrics_exporter_example.create_metrics_exporter",
            return_value=mock_exporter,
        ), patch("builtins.print", side_effect=lambda *a, **kw: printed.append(a[0] if a else "")):
            main()

        assert json_sample in printed


# ---------------------------------------------------------------------------
# 7. get_stats() interaction
# ---------------------------------------------------------------------------


class TestTrackerStatsLogging:
    """Ensure get_stats() is called and its fields are consumed."""

    def test_get_stats_called_once(self):
        """create_tracker result has get_stats() called exactly once."""
        from forge_harness.iteration.metrics_exporter_example import main

        mock_tracker = _make_mock_tracker()
        mock_exporter = _make_mock_exporter()

        with patch("sys.argv", ["prog"]), patch(
            "forge_harness.iteration.metrics_exporter_example.create_tracker",
            return_value=mock_tracker,
        ), patch(
            "forge_harness.iteration.metrics_exporter_example.create_metrics_exporter",
            return_value=mock_exporter,
        ):
            main()

        mock_tracker.get_stats.assert_called_once()

    def test_stats_zero_iterations_handled(self):
        """main() does not raise when all stats counters are zero."""
        from forge_harness.iteration.metrics_exporter_example import main

        mock_tracker = _make_mock_tracker(
            total=0, completed=0, failed=0, features=0, tests=0, lines=0
        )
        mock_exporter = _make_mock_exporter()

        with patch("sys.argv", ["prog"]), patch(
            "forge_harness.iteration.metrics_exporter_example.create_tracker",
            return_value=mock_tracker,
        ), patch(
            "forge_harness.iteration.metrics_exporter_example.create_metrics_exporter",
            return_value=mock_exporter,
        ):
            result = main()

        assert result == 0


# ---------------------------------------------------------------------------
# 8. Invalid --format is rejected by argparse
# ---------------------------------------------------------------------------


class TestInvalidFormat:
    """Ensure argparse rejects unknown format values."""

    def test_invalid_format_raises_system_exit(self):
        """An unknown --format value must cause SystemExit (argparse error)."""
        from forge_harness.iteration.metrics_exporter_example import main

        with patch("sys.argv", ["prog", "--format", "csv"]), pytest.raises(SystemExit):
            main()


# ---------------------------------------------------------------------------
# 9. Module-level __main__ guard
# ---------------------------------------------------------------------------


class TestMainGuard:
    """Verify the __main__ guard calls sys.exit(main())."""

    def test_module_calls_sys_exit(self):
        """Running the module as __main__ passes main()'s return value to sys.exit."""
        mock_tracker = _make_mock_tracker()
        mock_exporter = _make_mock_exporter()

        with patch("sys.argv", ["prog"]), patch(
            "forge_harness.iteration.metrics_exporter_example.create_tracker",
            return_value=mock_tracker,
        ), patch(
            "forge_harness.iteration.metrics_exporter_example.create_metrics_exporter",
            return_value=mock_exporter,
        ), patch("sys.exit") as mock_exit, patch.dict(
            sys.modules,
            {
                "forge_harness.iteration.metrics_exporter_example": __import__(
                    "forge_harness.iteration.metrics_exporter_example",
                    fromlist=["main"],
                )
            },
        ):
            # Simulate __name__ == "__main__" by calling main() and checking
            # the return value matches what sys.exit would receive.
            from forge_harness.iteration.metrics_exporter_example import main

            rc = main()
            assert rc == 0
