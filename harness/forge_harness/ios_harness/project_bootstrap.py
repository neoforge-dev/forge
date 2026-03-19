"""
Project Bootstrap Module
=========================

Generates new iOS projects from templates with FORGE conventions.

Architecture:
- Templates: base-swiftui, mvvm-standard, enterprise
- XcodeGen: YAML → .xcodeproj generation
- CLAUDE.md: Inject iOS agent rules
- Domain config: COPPA, HIPAA-lite, etc.
- Feature scaffolding: Auth, Dashboard, Settings
"""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Literal

import yaml

# Import from parent harness
from ..domain_config import get_domain_config

TemplateType = Literal["base-swiftui", "mvvm-standard", "enterprise"]


class FeatureType(Enum):
    """Available feature scaffolds."""

    AUTH = "auth"
    DASHBOARD = "dashboard"
    SETTINGS = "settings"
    ONBOARDING = "onboarding"
    PROFILE = "profile"


@dataclass
class ProjectSpec:
    """iOS project specification."""

    name: str  # Project name (e.g., "InterviewSimulator")
    bundle_id: str  # Bundle ID (e.g., "com.forge.codeswiftr.interview-simulator")
    domain: str  # FORGE domain (e.g., "codeswiftr-com")
    template: TemplateType  # Template to use
    features: list[str]  # Features to scaffold
    backend_url: str  # FastAPI endpoint URL
    deployment_target: str = "17.0"  # iOS minimum version
    swift_version: str = "6.0"  # Swift language version
    enable_swiftdata: bool = True  # Use SwiftData for persistence
    enable_coppa_compliance: bool = False  # Auto-set from domain
    marketing_version: str = "0.1.0"  # App version
    build_number: int = 1  # Build number


@dataclass
class BootstrapResult:
    """Result of project bootstrap."""

    project_path: Path
    template_used: TemplateType
    features_scaffolded: list[str]
    xcodegen_success: bool
    compile_success: bool
    created_files: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class ScaffoldResult:
    """Result of feature scaffolding."""

    feature_name: str
    files_created: list[str]
    success: bool
    errors: list[str] = field(default_factory=list)


class ProjectBootstrap:
    """Bootstrap iOS projects from templates."""

    def __init__(self, forge_root: Path, templates_dir: Path | None = None):
        """
        Initialize project bootstrap.

        Args:
            forge_root: Path to FORGE repository root
            templates_dir: Path to templates directory (defaults to forge_root/harness/templates/ios)
        """
        self.forge_root = Path(forge_root)
        self.templates_dir = templates_dir or (self.forge_root / "harness" / "templates" / "ios")

        if not self.templates_dir.exists():
            raise ValueError(f"Templates directory not found: {self.templates_dir}")

    async def bootstrap_project(
        self,
        spec: ProjectSpec,
        output_dir: Path,
        overwrite: bool = False,
    ) -> BootstrapResult:
        """
        Generate complete iOS project from template.

        Steps:
        1. Load template (base/mvvm/enterprise)
        2. Apply domain-specific config (COPPA, etc.)
        3. Generate XcodeGen project.yml
        4. Inject CLAUDE.md with iOS rules
        5. Generate AppConfig with backend URL
        6. Scaffold requested features
        7. Run xcodegen generate
        8. Validate project compiles

        Args:
            spec: Project specification
            output_dir: Where to create the project
            overwrite: Whether to overwrite existing project

        Returns:
            BootstrapResult with status and created files
        """
        result = BootstrapResult(
            project_path=output_dir,
            template_used=spec.template,
            features_scaffolded=[],
            xcodegen_success=False,
            compile_success=False,
        )

        try:
            # Step 1: Validate and prepare
            if output_dir.exists() and not overwrite:
                result.errors.append(f"Output directory already exists: {output_dir}")
                return result

            output_dir.mkdir(parents=True, exist_ok=True)

            # Step 2: Copy template
            template_path = self.templates_dir / spec.template
            if not template_path.exists():
                result.errors.append(f"Template not found: {template_path}")
                return result

            copied_files = await self._copy_template(template_path, output_dir)
            result.created_files.extend(copied_files)
            result.warnings.append(f"Copied template from {template_path}")

            # Step 3: Apply domain config
            domain_config = get_domain_config(spec.domain)
            if domain_config.coppa_required:
                spec.enable_coppa_compliance = True
                result.warnings.append("COPPA compliance enabled (domain requirement)")

            # Step 4: Generate XcodeGen project.yml
            project_yml = self._generate_project_yml(spec)
            project_yml_path = output_dir / "project.yml"
            project_yml_path.write_text(yaml.dump(project_yml, sort_keys=False))
            result.created_files.append(str(project_yml_path))

            # Step 5: Inject CLAUDE.md
            claude_md = self._generate_claude_md(spec, domain_config)
            claude_md_path = output_dir / "CLAUDE.md"
            claude_md_path.write_text(claude_md)
            result.created_files.append(str(claude_md_path))

            # Step 6: Generate AppConfig.swift
            app_config = self._generate_app_config(spec)
            config_dir = output_dir / "App" / "Core" / "Config"
            config_dir.mkdir(parents=True, exist_ok=True)
            config_path = config_dir / "AppConfig.swift"
            config_path.write_text(app_config)
            result.created_files.append(str(config_path))

            # Step 7: Scaffold features
            for feature in spec.features:
                scaffold_result = await self.scaffold_feature(
                    feature_name=feature,
                    project_dir=output_dir,
                    spec=spec,
                )
                if scaffold_result.success:
                    result.features_scaffolded.append(feature)
                    result.created_files.extend(scaffold_result.files_created)
                else:
                    result.warnings.extend(
                        [f"Feature {feature}: {e}" for e in scaffold_result.errors]
                    )

            # Step 8: Run xcodegen
            xcodegen_result = await self._run_xcodegen(output_dir)
            result.xcodegen_success = xcodegen_result["success"]
            if not xcodegen_result["success"]:
                result.errors.append(f"XcodeGen failed: {xcodegen_result['error']}")

            # Step 9: Validate compilation (optional, requires Xcode)
            # Skip for now - can be added later
            result.compile_success = False  # Not attempted

        except Exception as e:
            result.errors.append(f"Bootstrap failed: {str(e)}")

        return result

    async def scaffold_feature(
        self,
        feature_name: str,
        project_dir: Path,
        spec: ProjectSpec | None = None,
    ) -> ScaffoldResult:
        """
        Scaffold a feature using MVVM pattern.

        Generates:
        - Models (SwiftData if needed)
        - Service protocol + implementation
        - ViewModel (@Observable)
        - Views (SwiftUI)
        - Tests (XCTest)
        - Preview mocks

        Args:
            feature_name: Name of feature (auth, dashboard, settings)
            project_dir: Project root directory
            spec: Optional project spec for customization

        Returns:
            ScaffoldResult with created files
        """
        result = ScaffoldResult(
            feature_name=feature_name,
            files_created=[],
            success=False,
        )

        try:
            # Validate feature type
            feature_type = self._validate_feature(feature_name)
            if not feature_type:
                result.errors.append(f"Unknown feature: {feature_name}")
                return result

            # Create feature directory
            features_dir = project_dir / "App" / "Features"
            feature_dir = features_dir / feature_name.capitalize()
            feature_dir.mkdir(parents=True, exist_ok=True)

            # Generate feature files based on type
            if feature_type == FeatureType.AUTH:
                files = await self._scaffold_auth_feature(feature_dir, spec)
            elif feature_type == FeatureType.DASHBOARD:
                files = await self._scaffold_dashboard_feature(feature_dir, spec)
            elif feature_type == FeatureType.SETTINGS:
                files = await self._scaffold_settings_feature(feature_dir, spec)
            else:
                files = await self._scaffold_generic_feature(feature_dir, feature_name, spec)

            result.files_created = [str(f) for f in files]
            result.success = True

        except Exception as e:
            result.errors.append(f"Scaffold failed: {str(e)}")

        return result

    async def _copy_template(self, template_path: Path, output_dir: Path) -> list[str]:
        """Copy template directory to output."""
        copied_files = []
        for item in template_path.rglob("*"):
            if item.is_file() and not item.name.startswith("."):
                rel_path = item.relative_to(template_path)
                dest = output_dir / rel_path
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, dest)
                copied_files.append(str(dest))
        return copied_files

    def _generate_project_yml(self, spec: ProjectSpec) -> dict[str, Any]:
        """Generate XcodeGen project.yml configuration."""
        return {
            "name": spec.name,
            "options": {
                "bundleIdPrefix": ".".join(spec.bundle_id.split(".")[:-1]),
                "deploymentTarget": {"iOS": spec.deployment_target},
                "xcodeVersion": "15.0",
            },
            "settings": {
                "base": {
                    "PRODUCT_BUNDLE_IDENTIFIER": spec.bundle_id,
                    "CURRENT_PROJECT_VERSION": str(spec.build_number),
                    "MARKETING_VERSION": spec.marketing_version,
                    "SWIFT_VERSION": spec.swift_version,
                }
            },
            "targets": {
                spec.name: {
                    "type": "application",
                    "platform": "iOS",
                    "sources": [{"path": "App"}],
                    "settings": {
                        "base": {
                            "INFOPLIST_FILE": "App/Info.plist",
                            "DEVELOPMENT_ASSET_PATHS": '"App/Preview Content"',
                        }
                    },
                    "scheme": {"testTargets": [f"{spec.name}Tests"]},
                },
                f"{spec.name}Tests": {
                    "type": "bundle.unit-test",
                    "platform": "iOS",
                    "sources": [{"path": "Tests"}],
                    "dependencies": [{"target": spec.name}],
                },
            },
        }

    def _generate_claude_md(self, spec: ProjectSpec, domain_config: Any) -> str:
        """Generate CLAUDE.md with iOS-specific rules."""
        rules = f"""# Claude Code playbook for {spec.name}

Goals:
- Keep the app compiling at all times.
- Follow iOS 17+ APIs: Observation (@Observable) and SwiftData.
- Use Swift 6 strict concurrency (actors for shared state).
- No UIKit imports - SwiftUI only.
- Backend integration with {spec.backend_url}

Architecture:
- MVVM pattern: View → ViewModel (@Observable) → Service (actor)
- Dependency injection via Composition.swift
- SwiftData for local persistence
- URLSession for networking (no third-party libs)
- Keychain for secure token storage

Workflow:
1) Plan mode for multi-file changes (feature scaffolds, refactors).
2) Execute in small steps; run tests after each step.
3) If a dependency is required, propose it and update Docs/Dependencies.md.

Definition of done (per feature):
- Feature compiles
- Loading/error/empty states included
- Basic unit tests updated/added
- SwiftUI previews working
"""

        # Add domain-specific rules
        if domain_config.coppa_required:
            rules += """
COPPA Compliance (CRITICAL):
- Implement age gate on first launch
- Require parental consent for users <13
- Store consent proof in Keychain
- No third-party ads or tracking for <13
- Parental consent workflow before ANY data collection
"""

        if domain_config.hipaa_lite:
            rules += """
HIPAA-Lite Compliance:
- Anonymize all personal health data
- Encrypt data at rest (SwiftData)
- Encrypt data in transit (TLS only)
- No logging of PHI
- Secure token management (Keychain)
"""

        return rules

    def _generate_app_config(self, spec: ProjectSpec) -> str:
        """Generate AppConfig.swift with backend URL and settings."""
        return f'''import Foundation

enum AppConfig {{
    static let backendURL = "{spec.backend_url}"
    static let bundleID = "{spec.bundle_id}"
    static let version = "{spec.marketing_version}"
    static let buildNumber = {spec.build_number}

    // Feature flags
    static let enableSwiftData = {str(spec.enable_swiftdata).lower()}
    static let enableCOPPACompliance = {str(spec.enable_coppa_compliance).lower()}

    // API endpoints
    static let authEndpoint = "\\(backendURL)/auth"
    static let apiEndpoint = "\\(backendURL)/api"
}}
'''

    async def _run_xcodegen(self, project_dir: Path) -> dict[str, Any]:
        """Run xcodegen to generate .xcodeproj."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "xcodegen",
                "generate",
                cwd=project_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()

            return {
                "success": proc.returncode == 0,
                "stdout": stdout.decode() if stdout else "",
                "stderr": stderr.decode() if stderr else "",
                "error": stderr.decode() if proc.returncode != 0 else None,
            }
        except FileNotFoundError:
            return {
                "success": False,
                "error": "xcodegen not found. Install with: brew install xcodegen",
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _validate_feature(self, feature_name: str) -> FeatureType | None:
        """Validate and return feature type."""
        try:
            return FeatureType(feature_name.lower())
        except ValueError:
            return None

    async def _scaffold_auth_feature(
        self, feature_dir: Path, spec: ProjectSpec | None
    ) -> list[Path]:
        """Scaffold authentication feature."""
        from .feature_scaffold import FeatureScaffold

        if not spec:
            return []

        scaffold = FeatureScaffold(spec.name, spec.backend_url)
        result = await scaffold.scaffold_auth(feature_dir.parent.parent.parent)
        return [Path(f) for f in result.files_created]

    async def _scaffold_dashboard_feature(
        self, feature_dir: Path, spec: ProjectSpec | None
    ) -> list[Path]:
        """Scaffold dashboard feature."""
        from .feature_scaffold import FeatureScaffold

        if not spec:
            return []

        scaffold = FeatureScaffold(spec.name, spec.backend_url)
        result = await scaffold.scaffold_dashboard(feature_dir.parent.parent.parent)
        return [Path(f) for f in result.files_created]

    async def _scaffold_settings_feature(
        self, feature_dir: Path, spec: ProjectSpec | None
    ) -> list[Path]:
        """Scaffold settings feature."""
        from .feature_scaffold import FeatureScaffold

        if not spec:
            return []

        scaffold = FeatureScaffold(spec.name, spec.backend_url)
        result = await scaffold.scaffold_settings(feature_dir.parent.parent.parent)
        return [Path(f) for f in result.files_created]

    async def _scaffold_generic_feature(
        self, feature_dir: Path, feature_name: str, spec: ProjectSpec | None
    ) -> list[Path]:
        """Scaffold generic feature with basic MVVM structure."""
        # For unsupported features, create basic structure
        (feature_dir / f"{feature_name.capitalize()}View.swift").write_text(
            f'// {feature_name.capitalize()}View.swift\nimport SwiftUI\n\nstruct {feature_name.capitalize()}View: View {{\n    var body: some View {{\n        Text("{feature_name}")\n    }}\n}}\n'
        )
        return [feature_dir / f"{feature_name.capitalize()}View.swift"]


def select_template(project_complexity: str, domain: str) -> TemplateType:
    """
    Select template based on project needs.

    Rules:
    - base-swiftui: Prototypes, simple apps, learning
    - mvvm-standard: Most FORGE MVPs (default)
    - enterprise: Complex apps (TheBrightHarbor with COPPA)

    Args:
        project_complexity: "prototype" | "mvp" | "enterprise"
        domain: FORGE domain name

    Returns:
        Template type to use
    """
    domain_config = get_domain_config(domain)

    if domain_config.coppa_required or domain_config.hipaa_lite:
        return "enterprise"  # Need compliance flows

    if project_complexity == "prototype":
        return "base-swiftui"

    return "mvvm-standard"  # Default for MVPs
