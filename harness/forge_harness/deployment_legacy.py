"""
FORGE Deployment Automation (Legacy)
=====================================

Handles deployment to Railway (backend) and Cloudflare Pages (frontend).
Includes quality gates, health checks, and verification.

Note: This is the legacy deployment module. For the new pluggable provider
system, see forge_harness.deployment package.
"""

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx


@dataclass
class QualityGateResult:
    """Result of quality gate checks."""

    passed: bool
    backend_coverage: float | None
    frontend_build_ok: bool
    backend_tests_passed: int
    backend_tests_failed: int
    frontend_tests_passed: int
    frontend_tests_failed: int
    errors: list[str]


@dataclass
class DeploymentResult:
    """Result of a deployment operation."""

    success: bool
    target: str  # "railway" or "cloudflare"
    url: str | None
    duration_seconds: float
    error: str | None
    logs: str


@dataclass
class SmokeTestResult:
    """Result of post-deployment smoke tests."""

    passed: bool
    tests_run: int
    tests_passed: int
    tests_failed: int
    results: list[dict[str, str | bool | int]]
    errors: list[str]

    @property
    def summary(self) -> str:
        """Generate human-readable summary."""
        status = "PASSED" if self.passed else "FAILED"
        return f"Smoke Tests: {status} ({self.tests_passed}/{self.tests_run} passed)"


class ForgeDeployer:
    """
    Deployment automation for FORGE projects.

    Handles:
    - Quality gate verification (coverage, tests, build)
    - Backend deployment to Railway
    - Frontend deployment to Cloudflare Pages
    - Health check verification
    """

    # Minimum coverage requirements
    MIN_BACKEND_COVERAGE = 70.0
    MIN_FRONTEND_COVERAGE = 60.0

    def __init__(
        self,
        domain: str,
        project: str,
        project_dir: Path,
        railway_token: str | None = None,
        cloudflare_token: str | None = None,
        cloudflare_account_id: str | None = None,
    ):
        """
        Initialize deployer.

        Args:
            domain: FORGE domain name
            project: Project name
            project_dir: Path to project root (e.g., /path/to/codeswiftr-com/interview-simulator)
            railway_token: Railway API token (defaults to RAILWAY_TOKEN env var)
            cloudflare_token: Cloudflare API token (defaults to CLOUDFLARE_API_TOKEN env var)
            cloudflare_account_id: Cloudflare account ID (defaults to CLOUDFLARE_ACCOUNT_ID env var)
        """
        self.domain = domain
        self.project = project
        self.project_dir = Path(project_dir)
        self.backend_dir = self.project_dir / "backend"
        self.frontend_dir = self.project_dir / "frontend"

        self.railway_token = railway_token or os.environ.get("RAILWAY_TOKEN")
        self.cloudflare_token = cloudflare_token or os.environ.get("CLOUDFLARE_API_TOKEN")
        self.cloudflare_account_id = cloudflare_account_id or os.environ.get(
            "CLOUDFLARE_ACCOUNT_ID"
        )

    def _run_command(
        self,
        cmd: list[str],
        cwd: Path,
        env: dict[str, str] | None = None,
        timeout: int = 300,
    ) -> subprocess.CompletedProcess:
        """Run a command with proper environment."""
        run_env = os.environ.copy()
        if env:
            run_env.update(env)

        return subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=run_env,
        )

    def check_quality_gates(self) -> QualityGateResult:
        """
        Run all quality gate checks before deployment.

        Returns:
            QualityGateResult with pass/fail status and metrics
        """
        errors = []
        backend_coverage = None
        backend_passed = 0
        backend_failed = 0
        frontend_passed = 0
        frontend_failed = 0
        frontend_build_ok = False

        # Backend tests with coverage
        if self.backend_dir.exists():
            result = self._run_command(
                ["uv", "run", "pytest", "--cov=app", "--cov-report=term-missing", "-q"],
                cwd=self.backend_dir,
                timeout=180,
            )

            if result.returncode != 0:
                errors.append(f"Backend tests failed: {result.stderr}")
            else:
                # Parse coverage from output
                for line in result.stdout.split("\n"):
                    if "TOTAL" in line:
                        parts = line.split()
                        for part in parts:
                            if part.endswith("%"):
                                try:
                                    backend_coverage = float(part.rstrip("%"))
                                except ValueError:
                                    pass

                    # Parse test counts (e.g., "45 passed, 2 failed")
                    if "passed" in line:
                        import re

                        passed_match = re.search(r"(\d+) passed", line)
                        failed_match = re.search(r"(\d+) failed", line)
                        if passed_match:
                            backend_passed = int(passed_match.group(1))
                        if failed_match:
                            backend_failed = int(failed_match.group(1))

                if backend_coverage is not None and backend_coverage < self.MIN_BACKEND_COVERAGE:
                    errors.append(
                        f"Backend coverage {backend_coverage}% below minimum {self.MIN_BACKEND_COVERAGE}%"
                    )

        # Frontend tests
        if self.frontend_dir.exists():
            result = self._run_command(
                ["npm", "run", "test", "--", "--run"],
                cwd=self.frontend_dir,
                timeout=120,
            )

            if result.returncode != 0:
                errors.append(f"Frontend tests failed: {result.stderr}")
            else:
                # Parse test counts from vitest output
                import re

                passed_match = re.search(r"(\d+) passed", result.stdout)
                failed_match = re.search(r"(\d+) failed", result.stdout)
                if passed_match:
                    frontend_passed = int(passed_match.group(1))
                if failed_match:
                    frontend_failed = int(failed_match.group(1))

            # Frontend build check
            build_result = self._run_command(
                ["npm", "run", "build"],
                cwd=self.frontend_dir,
                timeout=180,
            )

            if build_result.returncode == 0:
                frontend_build_ok = True
            else:
                errors.append(f"Frontend build failed: {build_result.stderr}")

        return QualityGateResult(
            passed=len(errors) == 0,
            backend_coverage=backend_coverage,
            frontend_build_ok=frontend_build_ok,
            backend_tests_passed=backend_passed,
            backend_tests_failed=backend_failed,
            frontend_tests_passed=frontend_passed,
            frontend_tests_failed=frontend_failed,
            errors=errors,
        )

    def deploy_backend(self) -> DeploymentResult:
        """
        Deploy backend to Railway.

        Returns:
            DeploymentResult with success status and deployment URL
        """
        if not self.railway_token:
            return DeploymentResult(
                success=False,
                target="railway",
                url=None,
                duration_seconds=0,
                error="RAILWAY_TOKEN not configured",
                logs="",
            )

        if not self.backend_dir.exists():
            return DeploymentResult(
                success=False,
                target="railway",
                url=None,
                duration_seconds=0,
                error=f"Backend directory not found: {self.backend_dir}",
                logs="",
            )

        start_time = time.time()

        result = self._run_command(
            ["railway", "up", "--detach"],
            cwd=self.backend_dir,
            env={"RAILWAY_TOKEN": self.railway_token},
            timeout=300,
        )

        duration = time.time() - start_time

        if result.returncode != 0:
            return DeploymentResult(
                success=False,
                target="railway",
                url=None,
                duration_seconds=duration,
                error=result.stderr,
                logs=result.stdout,
            )

        # Extract deployment URL from output
        url = None
        for line in result.stdout.split("\n"):
            if "https://" in line and "railway" in line.lower():
                import re

                url_match = re.search(r"(https://[^\s]+)", line)
                if url_match:
                    url = url_match.group(1)
                    break

        return DeploymentResult(
            success=True,
            target="railway",
            url=url,
            duration_seconds=duration,
            error=None,
            logs=result.stdout,
        )

    def deploy_frontend(self) -> DeploymentResult:
        """
        Deploy frontend to Cloudflare Pages.

        Returns:
            DeploymentResult with success status and deployment URL
        """
        if not self.cloudflare_token or not self.cloudflare_account_id:
            return DeploymentResult(
                success=False,
                target="cloudflare",
                url=None,
                duration_seconds=0,
                error="Cloudflare credentials not configured",
                logs="",
            )

        if not self.frontend_dir.exists():
            return DeploymentResult(
                success=False,
                target="cloudflare",
                url=None,
                duration_seconds=0,
                error=f"Frontend directory not found: {self.frontend_dir}",
                logs="",
            )

        start_time = time.time()

        # Build first (if not already built)
        dist_dir = self.frontend_dir / "dist"
        if not dist_dir.exists():
            build_result = self._run_command(
                ["npm", "run", "build"],
                cwd=self.frontend_dir,
                timeout=180,
            )
            if build_result.returncode != 0:
                return DeploymentResult(
                    success=False,
                    target="cloudflare",
                    url=None,
                    duration_seconds=time.time() - start_time,
                    error=f"Build failed: {build_result.stderr}",
                    logs=build_result.stdout,
                )

        # Deploy with wrangler
        project_name = f"{self.project.replace('-', '')}-app"

        result = self._run_command(
            [
                "wrangler",
                "pages",
                "deploy",
                "dist",
                f"--project-name={project_name}",
                "--branch=main",
            ],
            cwd=self.frontend_dir,
            env={
                "CLOUDFLARE_API_TOKEN": self.cloudflare_token,
                "CLOUDFLARE_ACCOUNT_ID": self.cloudflare_account_id,
            },
            timeout=300,
        )

        duration = time.time() - start_time

        if result.returncode != 0:
            return DeploymentResult(
                success=False,
                target="cloudflare",
                url=None,
                duration_seconds=duration,
                error=result.stderr,
                logs=result.stdout,
            )

        # Extract deployment URL
        url = None
        for line in result.stdout.split("\n"):
            if "https://" in line:
                import re

                url_match = re.search(r"(https://[^\s]+\.pages\.dev[^\s]*)", line)
                if url_match:
                    url = url_match.group(1)
                    break

        return DeploymentResult(
            success=True,
            target="cloudflare",
            url=url,
            duration_seconds=duration,
            error=None,
            logs=result.stdout,
        )

    def verify_deployment(
        self,
        backend_url: str | None = None,
        frontend_url: str | None = None,
        timeout: int = 30,
    ) -> dict[str, Any]:
        """
        Verify deployments are healthy.

        Args:
            backend_url: Backend URL to check (e.g., https://app.railway.app)
            frontend_url: Frontend URL to check (e.g., https://app.pages.dev)
            timeout: Request timeout in seconds

        Returns:
            Dict with verification results for each target
        """
        results = {}

        if backend_url:
            try:
                # Check health endpoint
                health_url = f"{backend_url.rstrip('/')}/health"
                resp = httpx.get(health_url, timeout=timeout, follow_redirects=True)
                results["backend"] = {
                    "healthy": resp.status_code == 200,
                    "status_code": resp.status_code,
                    "url": backend_url,
                    "error": None,
                }
            except Exception as e:
                results["backend"] = {
                    "healthy": False,
                    "status_code": None,
                    "url": backend_url,
                    "error": str(e),
                }

        if frontend_url:
            try:
                resp = httpx.get(frontend_url, timeout=timeout, follow_redirects=True)
                results["frontend"] = {
                    "healthy": resp.status_code == 200,
                    "status_code": resp.status_code,
                    "url": frontend_url,
                    "error": None,
                }
            except Exception as e:
                results["frontend"] = {
                    "healthy": False,
                    "status_code": None,
                    "url": frontend_url,
                    "error": str(e),
                }

        return results

    def run_smoke_tests(
        self,
        backend_url: str | None = None,
        frontend_url: str | None = None,
        timeout: int = 30,
    ) -> SmokeTestResult:
        """
        Run comprehensive smoke tests against deployed endpoints.

        Tests include:
        - Backend health check (/health)
        - Backend readiness check (/api/v1/health/ready)
        - Backend version endpoint (/api/v1/version)
        - Frontend loads successfully
        - Frontend returns valid HTML

        Args:
            backend_url: Backend URL to test
            frontend_url: Frontend URL to test
            timeout: Request timeout in seconds

        Returns:
            SmokeTestResult with detailed test results
        """
        results: list[dict[str, str | bool | int]] = []
        errors: list[str] = []

        # Backend smoke tests
        if backend_url:
            base_url = backend_url.rstrip("/")

            # Test: Health check
            try:
                resp = httpx.get(f"{base_url}/health", timeout=timeout, follow_redirects=True)
                results.append(
                    {
                        "name": "backend_health",
                        "passed": resp.status_code == 200,
                        "status_code": resp.status_code,
                        "url": f"{base_url}/health",
                    }
                )
                if resp.status_code != 200:
                    errors.append(f"Health check failed: expected 200, got {resp.status_code}")
            except Exception as e:
                results.append(
                    {
                        "name": "backend_health",
                        "passed": False,
                        "status_code": 0,
                        "url": f"{base_url}/health",
                    }
                )
                errors.append(f"Health check failed: {e}")

            # Test: Readiness check
            try:
                resp = httpx.get(
                    f"{base_url}/api/v1/health/ready",
                    timeout=timeout,
                    follow_redirects=True,
                )
                # Accept 200 or 404 (endpoint may not exist)
                passed = resp.status_code in (200, 404)
                results.append(
                    {
                        "name": "backend_readiness",
                        "passed": passed,
                        "status_code": resp.status_code,
                        "url": f"{base_url}/api/v1/health/ready",
                    }
                )
                if not passed:
                    errors.append(f"Readiness check failed: got {resp.status_code}")
            except Exception as e:
                results.append(
                    {
                        "name": "backend_readiness",
                        "passed": False,
                        "status_code": 0,
                        "url": f"{base_url}/api/v1/health/ready",
                    }
                )
                errors.append(f"Readiness check failed: {e}")

            # Test: API responds (check OpenAPI docs)
            try:
                resp = httpx.get(
                    f"{base_url}/docs",
                    timeout=timeout,
                    follow_redirects=True,
                )
                # Accept 200 or 404 (docs may be disabled)
                passed = resp.status_code in (200, 404)
                results.append(
                    {
                        "name": "backend_api_docs",
                        "passed": passed,
                        "status_code": resp.status_code,
                        "url": f"{base_url}/docs",
                    }
                )
            except Exception:
                results.append(
                    {
                        "name": "backend_api_docs",
                        "passed": False,
                        "status_code": 0,
                        "url": f"{base_url}/docs",
                    }
                )
                # Don't add to errors - docs check is optional

        # Frontend smoke tests
        if frontend_url:
            # Test: Frontend loads
            try:
                resp = httpx.get(frontend_url, timeout=timeout, follow_redirects=True)
                results.append(
                    {
                        "name": "frontend_loads",
                        "passed": resp.status_code == 200,
                        "status_code": resp.status_code,
                        "url": frontend_url,
                    }
                )
                if resp.status_code != 200:
                    errors.append(f"Frontend load failed: expected 200, got {resp.status_code}")
            except Exception as e:
                results.append(
                    {
                        "name": "frontend_loads",
                        "passed": False,
                        "status_code": 0,
                        "url": frontend_url,
                    }
                )
                errors.append(f"Frontend load failed: {e}")

            # Test: Frontend returns HTML
            try:
                resp = httpx.get(frontend_url, timeout=timeout, follow_redirects=True)
                has_html = "<!DOCTYPE html>" in resp.text or "<html" in resp.text.lower()
                results.append(
                    {
                        "name": "frontend_html_valid",
                        "passed": has_html,
                        "status_code": resp.status_code,
                        "url": frontend_url,
                    }
                )
                if not has_html:
                    errors.append("Frontend did not return valid HTML content")
            except Exception as e:
                results.append(
                    {
                        "name": "frontend_html_valid",
                        "passed": False,
                        "status_code": 0,
                        "url": frontend_url,
                    }
                )
                errors.append(f"Frontend HTML check failed: {e}")

            # Test: Check for JavaScript bundle (SPA indicator)
            try:
                resp = httpx.get(frontend_url, timeout=timeout, follow_redirects=True)
                has_js = "script" in resp.text.lower() and (
                    "src=" in resp.text or 'type="module"' in resp.text
                )
                results.append(
                    {
                        "name": "frontend_has_js",
                        "passed": has_js,
                        "status_code": resp.status_code,
                        "url": frontend_url,
                    }
                )
            except Exception:
                results.append(
                    {
                        "name": "frontend_has_js",
                        "passed": False,
                        "status_code": 0,
                        "url": frontend_url,
                    }
                )
                # Don't add to errors - this is informational

        # Calculate results
        tests_run = len(results)
        tests_passed = sum(1 for r in results if r.get("passed"))
        tests_failed = tests_run - tests_passed

        # Determine overall pass/fail
        # Only fail on critical errors (health/load checks)
        critical_tests = ["backend_health", "frontend_loads"]
        critical_passed = all(
            r.get("passed", False) for r in results if r.get("name") in critical_tests
        )

        return SmokeTestResult(
            passed=critical_passed and len(errors) == 0,
            tests_run=tests_run,
            tests_passed=tests_passed,
            tests_failed=tests_failed,
            results=results,
            errors=errors,
        )

    def full_deploy(
        self,
        skip_quality_gates: bool = False,
        backend_only: bool = False,
        frontend_only: bool = False,
    ) -> dict[str, Any]:
        """
        Run full deployment pipeline.

        Args:
            skip_quality_gates: Skip quality gate checks (dangerous!)
            backend_only: Only deploy backend
            frontend_only: Only deploy frontend

        Returns:
            Dict with complete deployment results
        """
        results: dict[str, Any] = {
            "success": True,
            "quality_gates": None,
            "backend": None,
            "frontend": None,
            "verification": None,
        }

        # Quality gates
        if not skip_quality_gates:
            gates = self.check_quality_gates()
            results["quality_gates"] = {
                "passed": gates.passed,
                "backend_coverage": gates.backend_coverage,
                "frontend_build_ok": gates.frontend_build_ok,
                "errors": gates.errors,
            }

            if not gates.passed:
                results["success"] = False
                return results

        # Deploy backend
        backend_url = None
        if not frontend_only:
            backend_result = self.deploy_backend()
            results["backend"] = {
                "success": backend_result.success,
                "url": backend_result.url,
                "duration_seconds": backend_result.duration_seconds,
                "error": backend_result.error,
            }
            if not backend_result.success:
                results["success"] = False
            backend_url = backend_result.url

        # Deploy frontend
        frontend_url = None
        if not backend_only:
            frontend_result = self.deploy_frontend()
            results["frontend"] = {
                "success": frontend_result.success,
                "url": frontend_result.url,
                "duration_seconds": frontend_result.duration_seconds,
                "error": frontend_result.error,
            }
            if not frontend_result.success:
                results["success"] = False
            frontend_url = frontend_result.url

        # Verification and smoke tests
        if backend_url or frontend_url:
            # Wait a bit for deployments to be ready
            time.sleep(10)

            # Basic health verification
            results["verification"] = self.verify_deployment(
                backend_url=backend_url,
                frontend_url=frontend_url,
            )

            # Comprehensive smoke tests
            smoke_results = self.run_smoke_tests(
                backend_url=backend_url,
                frontend_url=frontend_url,
            )
            results["smoke_tests"] = {
                "passed": smoke_results.passed,
                "summary": smoke_results.summary,
                "tests_run": smoke_results.tests_run,
                "tests_passed": smoke_results.tests_passed,
                "tests_failed": smoke_results.tests_failed,
                "results": smoke_results.results,
                "errors": smoke_results.errors,
            }

            # Update success based on verification and smoke tests
            if results["verification"]:
                for target, status in results["verification"].items():
                    if not status.get("healthy"):
                        results["success"] = False

            if not smoke_results.passed:
                results["success"] = False

        return results

    def deploy_with_provider(
        self,
        provider_name: str,
        dry_run: bool = False,
    ) -> "DeploymentResult":
        """
        Deploy using the new provider system.

        Args:
            provider_name: Provider identifier ("railway" or "cloudflare")
            dry_run: If True, simulate deployment without executing

        Returns:
            DeploymentResult from the provider

        Example:
            deployer = ForgeDeployer(domain="example", project="app", project_dir=Path("."))
            result = deployer.deploy_with_provider("railway", dry_run=True)
        """
        from .deployment import ProviderRegistry
        from .deployment.base import DeploymentConfig

        provider = ProviderRegistry.get(provider_name)

        # Determine target based on provider capabilities
        target = provider.capabilities.targets[0]

        config = DeploymentConfig(
            domain=self.domain,
            project=self.project,
            project_dir=self.project_dir,
            target=target,
            dry_run=dry_run,
        )

        return provider.deploy(config)
