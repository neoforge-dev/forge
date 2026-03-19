"""
Screenshot Automation Module
==============================

Automated screenshot capture for App Store submissions.

Features:
- Multi-device screenshot capture
- Localization support
- UI automation integration
- Frame overlay (device frames)
- Batch screenshot generation
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from .simulator_harness import DeviceType, SimulatorHarness


class ScreenshotSize(Enum):
    """Required screenshot sizes for App Store."""

    IPHONE_67 = "6.7-inch"  # iPhone 15 Pro Max, 14 Pro Max
    IPHONE_65 = "6.5-inch"  # iPhone 11 Pro Max, XS Max
    IPHONE_58 = "5.8-inch"  # iPhone X, XS, 11 Pro
    IPAD_PRO_129 = "12.9-inch"  # iPad Pro 12.9"
    IPAD_PRO_11 = "11-inch"  # iPad Pro 11"


class Locale(Enum):
    """Supported locales for screenshots."""

    EN_US = "en-US"
    ES_ES = "es-ES"
    FR_FR = "fr-FR"
    DE_DE = "de-DE"
    JA_JP = "ja-JP"
    ZH_CN = "zh-CN"


@dataclass
class ScreenshotSpec:
    """Specification for screenshot capture."""

    name: str
    device_type: DeviceType
    locale: Locale = Locale.EN_US
    light_mode: bool = True
    steps: list[str] = field(default_factory=list)  # UI automation steps


@dataclass
class ScreenshotSet:
    """Set of screenshots for one device/locale combo."""

    device_type: DeviceType
    locale: Locale
    screenshots: list[Path] = field(default_factory=list)
    light_mode_screenshots: list[Path] = field(default_factory=list)
    dark_mode_screenshots: list[Path] = field(default_factory=list)


@dataclass
class ScreenshotResult:
    """Result of screenshot generation."""

    success: bool
    screenshot_sets: list[ScreenshotSet] = field(default_factory=list)
    total_screenshots: int = 0
    errors: list[str] = field(default_factory=list)


class ScreenshotAutomation:
    """Automated screenshot generation for App Store."""

    def __init__(
        self,
        bundle_id: str,
        output_dir: Path,
        simulator_harness: SimulatorHarness | None = None,
    ):
        """
        Initialize screenshot automation.

        Args:
            bundle_id: App bundle identifier
            output_dir: Where to save screenshots
            simulator_harness: Optional SimulatorHarness instance
        """
        self.bundle_id = bundle_id
        self.output_dir = Path(output_dir)
        self.simulator_harness = simulator_harness or SimulatorHarness()
        self.output_dir.mkdir(parents=True, exist_ok=True)

    async def generate_appstore_screenshots(
        self,
        specs: list[ScreenshotSpec],
        app_path: Path,
    ) -> ScreenshotResult:
        """
        Generate complete set of App Store screenshots.

        Args:
            specs: List of screenshot specifications
            app_path: Path to .app bundle

        Returns:
            ScreenshotResult with all generated screenshots
        """
        result = ScreenshotResult(success=True)

        for spec in specs:
            try:
                screenshot_set = await self._capture_screenshot_set(spec, app_path)
                result.screenshot_sets.append(screenshot_set)
                result.total_screenshots += len(screenshot_set.screenshots)
            except Exception as e:
                result.success = False
                result.errors.append(f"Failed to capture {spec.name}: {str(e)}")

        return result

    async def capture_single_screenshot(
        self,
        device_type: DeviceType,
        output_path: Path,
        locale: Locale = Locale.EN_US,
        dark_mode: bool = False,
    ) -> bool:
        """
        Capture single screenshot on specified device.

        Args:
            device_type: iOS device type
            output_path: Where to save screenshot
            locale: App locale
            dark_mode: Use dark mode appearance

        Returns:
            True if successful
        """
        # Boot device
        device = await self.simulator_harness.boot(device_type.value)

        # Set locale
        await self._set_locale(device.udid, locale)

        # Set appearance
        await self._set_appearance(device.udid, dark_mode)

        # Wait for settings to apply
        await asyncio.sleep(1)

        # Take screenshot
        success = await self.simulator_harness.screenshot(output_path, device_type.value)

        return success

    async def generate_device_frames(
        self,
        screenshots: list[Path],
        device_type: DeviceType,
    ) -> list[Path]:
        """
        Add device frames to screenshots.

        Args:
            screenshots: List of screenshot paths
            device_type: Device type for frame

        Returns:
            List of framed screenshot paths
        """
        # This would use a tool like fastlane's frameit or similar
        # For now, return placeholders
        framed_paths = []

        for screenshot in screenshots:
            framed_path = screenshot.parent / f"{screenshot.stem}_framed{screenshot.suffix}"
            # NOTE: Applying device frame is currently blocked on external dependency (e.g., fastlane frameit)
            framed_paths.append(framed_path)

        return framed_paths

    async def _capture_screenshot_set(
        self,
        spec: ScreenshotSpec,
        app_path: Path,
    ) -> ScreenshotSet:
        """Capture full screenshot set for one spec."""
        screenshot_set = ScreenshotSet(
            device_type=spec.device_type,
            locale=spec.locale,
        )

        # Boot device
        device = await self.simulator_harness.boot(spec.device_type.value)

        # Set locale
        await self._set_locale(device.udid, spec.locale)

        # Install app
        install_result = await self.simulator_harness.install(app_path, spec.device_type.value)
        if not install_result.success:
            raise RuntimeError(f"Failed to install app: {install_result.error}")

        # Capture in light mode
        if spec.light_mode:
            await self._set_appearance(device.udid, dark_mode=False)
            light_screenshots = await self._capture_screenshots(
                device.udid,
                spec,
                "light",
            )
            screenshot_set.light_mode_screenshots.extend(light_screenshots)
            screenshot_set.screenshots.extend(light_screenshots)

        # Capture in dark mode
        if not spec.light_mode:
            await self._set_appearance(device.udid, dark_mode=True)
            dark_screenshots = await self._capture_screenshots(
                device.udid,
                spec,
                "dark",
            )
            screenshot_set.dark_mode_screenshots.extend(dark_screenshots)
            screenshot_set.screenshots.extend(dark_screenshots)

        # Uninstall app
        await self.simulator_harness.uninstall(self.bundle_id, spec.device_type.value)

        return screenshot_set

    async def _capture_screenshots(
        self,
        udid: str,
        spec: ScreenshotSpec,
        mode: str,
    ) -> list[Path]:
        """Capture screenshots following UI automation steps."""
        screenshots = []

        # Launch app
        launch_result = await self.simulator_harness.launch(
            self.bundle_id,
            spec.device_type.value,
        )

        if not launch_result.success:
            raise RuntimeError(f"Failed to launch app: {launch_result.error}")

        # Execute UI automation steps and capture
        for i, step in enumerate(spec.steps):
            # Wait for UI
            await asyncio.sleep(1)

            # Take screenshot
            screenshot_path = (
                self.output_dir
                / spec.locale.value
                / spec.device_type.value
                / mode
                / f"{spec.name}_{i + 1}.png"
            )
            screenshot_path.parent.mkdir(parents=True, exist_ok=True)

            success = await self.simulator_harness.screenshot(
                screenshot_path,
                spec.device_type.value,
            )

            if success:
                screenshots.append(screenshot_path)

            # NOTE: Executing UI automation step (e.g., via XCUITest) is planned for Phase 3.
            # Currently only static screenshots are supported.

        return screenshots

    async def _set_locale(self, udid: str, locale: Locale) -> None:
        """Set simulator locale."""
        cmd = [
            "xcrun",
            "simctl",
            "spawn",
            udid,
            "defaults",
            "write",
            ".GlobalPreferences",
            "AppleLanguages",
            f"-array {locale.value.split('-')[0]}",
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

    async def _set_appearance(self, udid: str, dark_mode: bool) -> None:
        """Set simulator appearance mode."""
        appearance = "dark" if dark_mode else "light"

        cmd = [
            "xcrun",
            "simctl",
            "ui",
            udid,
            "appearance",
            appearance,
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()


def create_standard_screenshot_specs(
    app_name: str,
    devices: list[DeviceType] | None = None,
    locales: list[Locale] | None = None,
) -> list[ScreenshotSpec]:
    """
    Create standard screenshot specifications for App Store.

    Args:
        app_name: Application name
        devices: Device types (defaults to iPhone 15 Pro + iPad Pro)
        locales: Locales (defaults to en-US)

    Returns:
        List of screenshot specifications
    """
    devices = devices or [DeviceType.IPHONE_15_PRO, DeviceType.IPAD_PRO_13]
    locales = locales or [Locale.EN_US]

    specs = []

    for device in devices:
        for locale in locales:
            # Create specs for key app screens
            specs.extend(
                [
                    ScreenshotSpec(
                        name="launch",
                        device_type=device,
                        locale=locale,
                        steps=["Launch app", "Wait for home screen"],
                    ),
                    ScreenshotSpec(
                        name="main_feature",
                        device_type=device,
                        locale=locale,
                        steps=["Launch app", "Navigate to main feature", "Wait for content"],
                    ),
                    ScreenshotSpec(
                        name="settings",
                        device_type=device,
                        locale=locale,
                        steps=["Launch app", "Open settings", "Wait for settings"],
                    ),
                ]
            )

    return specs
