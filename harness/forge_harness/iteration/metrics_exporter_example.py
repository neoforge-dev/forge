#!/usr/bin/env python3
"""Example usage of the Metrics Exporter.

This script demonstrates how to export iteration metrics in JSON and Prometheus formats.

Usage:
    # Export metrics for a project
    python -m forge_harness.iteration.metrics_exporter_example

    # Use with existing tracker data
    python -m forge_harness.iteration.metrics_exporter_example --history .forge/learning/iteration_history.json
"""

import sys
from argparse import ArgumentParser
from pathlib import Path

from forge_harness.iteration.metrics_exporter import create_metrics_exporter
from forge_harness.iteration.tracker import create_tracker
from forge_harness.logging_config import get_logger

logger = get_logger(__name__)


def main():
    """Run the metrics exporter example."""
    parser = ArgumentParser(description="Export iteration metrics")
    parser.add_argument(
        "--project",
        default="forge-harness",
        help="Project name for metric labels (default: forge-harness)",
    )
    parser.add_argument(
        "--history",
        type=Path,
        help="Path to iteration history JSON (default: .forge/learning/iteration_history.json)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Output directory for metrics files (default: .forge/metrics)",
    )
    parser.add_argument(
        "--format",
        choices=["json", "prometheus", "both"],
        default="both",
        help="Export format (default: both)",
    )
    args = parser.parse_args()

    # Create tracker with history
    tracker = create_tracker(storage_path=args.history)
    stats = tracker.get_stats()

    logger.info(f"Loaded iteration history: {stats.total_iterations} iterations")
    logger.info(f"  Completed: {stats.completed_iterations}")
    logger.info(f"  Failed: {stats.failed_iterations}")
    logger.info(f"  Features: {stats.total_features}")
    logger.info(f"  Tests: {stats.total_tests}")
    logger.info(f"  Lines: {stats.total_lines}")

    # Create exporter
    exporter = create_metrics_exporter(
        project_name=args.project,
        tracker=tracker,
        output_dir=args.output_dir,
    )

    # Export based on format choice
    if args.format == "json":
        json_path = exporter.write_json()
        logger.info(f"✓ Exported JSON metrics to: {json_path}")
    elif args.format == "prometheus":
        prom_path = exporter.write_prometheus()
        logger.info(f"✓ Exported Prometheus metrics to: {prom_path}")
    else:  # both
        paths = exporter.write_all()
        logger.info("✓ Exported metrics:")
        logger.info(f"  JSON: {paths['json']}")
        logger.info(f"  Prometheus: {paths['prometheus']}")

    # Display sample output
    logger.info("\n=== Sample JSON Output ===")
    print(exporter.to_json())

    logger.info("\n=== Sample Prometheus Output (first 20 lines) ===")
    prom_lines = exporter.to_prometheus().split("\n")[:20]
    print("\n".join(prom_lines))

    return 0


if __name__ == "__main__":
    sys.exit(main())
