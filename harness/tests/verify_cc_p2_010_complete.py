#!/usr/bin/env python3
"""
CC-P2-010: Orchestration Page Pipeline Status - Verification Script
====================================================================

Comprehensive verification that the pipeline status integration is working correctly.
"""

import subprocess
import sys
from pathlib import Path


def print_header(title: str) -> None:
    """Print a formatted section header."""
    print(f"\n{'=' * 80}")
    print(f"  {title}")
    print(f"{'=' * 80}\n")


def print_success(message: str) -> None:
    """Print success message."""
    print(f"✅ {message}")


def print_error(message: str) -> None:
    """Print error message."""
    print(f"❌ {message}")


def print_info(message: str) -> None:
    """Print info message."""
    print(f"ℹ️  {message}")


def check_file_exists(path: Path, description: str) -> bool:
    """Check if a file exists."""
    if path.exists():
        print_success(f"{description}: {path}")
        return True
    else:
        print_error(f"{description} not found: {path}")
        return False


def run_backend_tests() -> bool:
    """Run backend tests for pipeline API."""
    print_header("Backend Tests")

    test_file = Path(__file__).parent / "test_cc_p2_010_pipelines.py"

    if not check_file_exists(test_file, "Backend test file"):
        return False

    print_info("Running backend tests...")

    result = subprocess.run(
        ["python", "-m", "pytest", str(test_file), "-v", "--no-cov"],
        cwd=Path(__file__).parent.parent,
        capture_output=True,
        text=True,
    )

    if result.returncode == 0:
        # Count passed tests
        passed = result.stdout.count(" PASSED")
        print_success(f"Backend tests passed: {passed} tests")
        return True
    else:
        print_error("Backend tests failed")
        print(result.stdout[-500:])  # Last 500 chars
        return False


def run_frontend_tests() -> bool:
    """Run frontend tests for Orchestration page."""
    print_header("Frontend Tests")

    test_file = (
        Path(__file__).parent.parent
        / "command_center/src/pages/__tests__/Orchestration.pipelines.test.tsx"
    )

    if not check_file_exists(test_file, "Frontend test file"):
        return False

    print_info("Running frontend tests...")

    result = subprocess.run(
        ["npm", "test", "--", "--run", "Orchestration.pipelines"],
        cwd=Path(__file__).parent.parent / "command_center",
        capture_output=True,
        text=True,
    )

    if result.returncode == 0:
        # Count passed tests
        passed = result.stdout.count("✓")
        print_success(f"Frontend tests passed: {passed} tests")
        return True
    else:
        print_error("Frontend tests failed")
        print(result.stdout[-500:])  # Last 500 chars
        return False


def verify_file_structure() -> bool:
    """Verify all required files exist."""
    print_header("File Structure Verification")

    base_path = Path(__file__).parent.parent

    files_to_check = [
        (
            base_path / "forge_harness/pipeline_data.py",
            "PipelineDataReader implementation",
        ),
        (
            base_path / "forge_harness/webhook_server.py",
            "Webhook server with /api/pipelines",
        ),
        (
            base_path / "command_center/src/api/client.ts",
            "API client with Pipeline type",
        ),
        (
            base_path / "command_center/src/pages/Orchestration.tsx",
            "Orchestration page component",
        ),
        (
            base_path / "tests/test_cc_p2_010_pipelines.py",
            "Backend integration tests",
        ),
        (
            base_path / "command_center/src/pages/__tests__/Orchestration.pipelines.test.tsx",
            "Frontend component tests",
        ),
        (
            base_path / "command_center/CC-P2-010-COMPLETION.md",
            "Completion report",
        ),
        (
            base_path / "command_center/CC-P2-010-SUMMARY.md",
            "Summary document",
        ),
    ]

    all_exist = True
    for file_path, description in files_to_check:
        if not check_file_exists(file_path, description):
            all_exist = False

    return all_exist


def verify_api_client_types() -> bool:
    """Verify Pipeline type in API client is correct."""
    print_header("API Client Type Verification")

    client_path = Path(__file__).parent.parent / "command_center/src/api/client.ts"

    if not client_path.exists():
        print_error("API client file not found")
        return False

    content = client_path.read_text()

    required_fields = [
        "type: 'orchestration' | 'ralph_loop'",
        "current_step: number | null",
        "total_steps: number",
        "metadata: Record<string, unknown>",
    ]

    all_present = True
    for field in required_fields:
        if field in content:
            print_success(f"Pipeline type has required field: {field}")
        else:
            print_error(f"Pipeline type missing field: {field}")
            all_present = False

    return all_present


def verify_backend_endpoint() -> bool:
    """Verify webhook_server.py has /api/pipelines endpoint."""
    print_header("Backend Endpoint Verification")

    server_path = Path(__file__).parent.parent / "forge_harness/webhook_server.py"

    if not server_path.exists():
        print_error("Webhook server file not found")
        return False

    content = server_path.read_text()

    checks = [
        ('@app.get("/api/pipelines")', "GET /api/pipelines endpoint"),
        ('@app.get("/api/pipelines/recent")', "GET /api/pipelines/recent endpoint"),
        ('@app.get("/api/pipelines/stats")', "GET /api/pipelines/stats endpoint"),
        ("from .pipeline_data import", "Pipeline data reader import"),
    ]

    all_present = True
    for pattern, description in checks:
        if pattern in content:
            print_success(f"Found: {description}")
        else:
            print_error(f"Missing: {description}")
            all_present = False

    return all_present


def main():
    """Run all verification checks."""
    print_header("CC-P2-010: Pipeline Status Integration - Verification")

    checks = [
        ("File Structure", verify_file_structure),
        ("API Client Types", verify_api_client_types),
        ("Backend Endpoint", verify_backend_endpoint),
        ("Backend Tests", run_backend_tests),
        ("Frontend Tests", run_frontend_tests),
    ]

    results = {}
    for name, check_func in checks:
        try:
            results[name] = check_func()
        except Exception as e:
            print_error(f"{name} verification failed with exception: {e}")
            results[name] = False

    # Summary
    print_header("Verification Summary")

    all_passed = all(results.values())
    passed_count = sum(results.values())
    total_count = len(results)

    for name, passed in results.items():
        status = "✅ PASSED" if passed else "❌ FAILED"
        print(f"{status} - {name}")

    print(f"\nResults: {passed_count}/{total_count} checks passed")

    if all_passed:
        print_success("\n🎉 All verification checks passed!")
        print_success("CC-P2-010 implementation is complete and verified.")
        return 0
    else:
        print_error("\n⚠️  Some verification checks failed.")
        print_error("Please review the errors above and fix the issues.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
