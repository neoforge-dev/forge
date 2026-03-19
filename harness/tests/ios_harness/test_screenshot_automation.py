"""Tests for screenshot automation."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge_harness.ios_harness.screenshot_automation import (
    Locale,
    ScreenshotAutomation,
    ScreenshotResult,
    ScreenshotSet,
    ScreenshotSize,
    ScreenshotSpec,
    create_standard_screenshot_specs,
)
from forge_harness.ios_harness.simulator_harness import DeviceType


@pytest.fixture
def output_dir(tmp_path):
    """Create temporary output directory."""
    return tmp_path / "screenshots"


@pytest.fixture
def automation(output_dir):
    """Create ScreenshotAutomation instance."""
    return ScreenshotAutomation(
        bundle_id="com.test.app",
        output_dir=output_dir,
    )


@pytest.fixture
def mock_app(tmp_path):
    """Create mock app bundle."""
    app_path = tmp_path / "TestApp.app"
    app_path.mkdir()
    return app_path


class TestScreenshotAutomation:
    """Test ScreenshotAutomation class."""

    def test_init(self, automation, output_dir):
        """Should initialize with bundle ID and output directory."""
        assert automation.bundle_id == "com.test.app"
        assert automation.output_dir == output_dir
        assert output_dir.exists()

    def test_init_creates_output_dir(self, tmp_path):
        """Should create output directory if it doesn't exist."""
        output_dir = tmp_path / "new_screenshots"
        automation = ScreenshotAutomation(
            bundle_id="com.test.app",
            output_dir=output_dir,
        )

        assert output_dir.exists()

    @pytest.mark.asyncio
    async def test_capture_single_screenshot(self, automation, output_dir):
        """Should capture screenshot on specified device."""
        screenshot_path = output_dir / "test.png"

        with (
            patch.object(automation.simulator_harness, "boot") as mock_boot,
            patch.object(automation.simulator_harness, "screenshot") as mock_screenshot,
            patch.object(automation, "_set_locale") as mock_locale,
            patch.object(automation, "_set_appearance") as mock_appearance,
        ):
            # Mock returns
            mock_device = MagicMock()
            mock_device.udid = "ABC-123"
            mock_boot.return_value = mock_device
            mock_screenshot.return_value = True

            success = await automation.capture_single_screenshot(
                device_type=DeviceType.IPHONE_15_PRO,
                output_path=screenshot_path,
            )

            assert success is True
            mock_boot.assert_called_once()
            mock_locale.assert_called_once()
            mock_appearance.assert_called_once()
            mock_screenshot.assert_called_once()

    @pytest.mark.asyncio
    async def test_capture_single_screenshot_dark_mode(self, automation, output_dir):
        """Should capture screenshot in dark mode."""
        screenshot_path = output_dir / "test_dark.png"

        with (
            patch.object(automation.simulator_harness, "boot") as mock_boot,
            patch.object(automation.simulator_harness, "screenshot") as mock_screenshot,
            patch.object(automation, "_set_locale") as mock_locale,
            patch.object(automation, "_set_appearance") as mock_appearance,
        ):
            mock_device = MagicMock()
            mock_device.udid = "ABC-123"
            mock_boot.return_value = mock_device
            mock_screenshot.return_value = True

            success = await automation.capture_single_screenshot(
                device_type=DeviceType.IPHONE_15_PRO,
                output_path=screenshot_path,
                dark_mode=True,
            )

            assert success is True
            # Verify dark mode was set
            mock_appearance.assert_called_with("ABC-123", True)

    @pytest.mark.asyncio
    async def test_generate_appstore_screenshots(self, automation, mock_app):
        """Should generate complete screenshot set."""
        specs = [
            ScreenshotSpec(
                name="main",
                device_type=DeviceType.IPHONE_15_PRO,
                steps=["Launch app"],
            )
        ]

        with patch.object(automation, "_capture_screenshot_set") as mock_capture:
            mock_set = ScreenshotSet(
                device_type=DeviceType.IPHONE_15_PRO,
                locale=Locale.EN_US,
                screenshots=[Path("test.png")],
            )
            mock_capture.return_value = mock_set

            result = await automation.generate_appstore_screenshots(specs, mock_app)

            assert result.success is True
            assert len(result.screenshot_sets) == 1
            assert result.total_screenshots == 1

    @pytest.mark.asyncio
    async def test_generate_appstore_screenshots_handles_errors(self, automation, mock_app):
        """Should handle errors gracefully."""
        specs = [
            ScreenshotSpec(
                name="main",
                device_type=DeviceType.IPHONE_15_PRO,
                steps=["Launch app"],
            )
        ]

        with patch.object(automation, "_capture_screenshot_set") as mock_capture:
            mock_capture.side_effect = RuntimeError("Failed to boot device")

            result = await automation.generate_appstore_screenshots(specs, mock_app)

            assert result.success is False
            assert len(result.errors) > 0

    @pytest.mark.asyncio
    async def test_generate_device_frames(self, automation, tmp_path):
        """Should generate device frames for screenshots."""
        screenshots = [
            tmp_path / "screenshot1.png",
            tmp_path / "screenshot2.png",
        ]

        for screenshot in screenshots:
            screenshot.touch()

        framed = await automation.generate_device_frames(
            screenshots,
            DeviceType.IPHONE_15_PRO,
        )

        assert len(framed) == 2
        assert all("_framed" in str(path) for path in framed)

    @pytest.mark.asyncio
    async def test_set_locale(self, automation):
        """Should set simulator locale."""
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(return_value=(b"", b""))
            mock_exec.return_value = mock_proc

            await automation._set_locale("ABC-123", Locale.ES_ES)

            mock_exec.assert_called_once()
            call_args = mock_exec.call_args[0]
            assert "defaults" in call_args
            assert "AppleLanguages" in call_args

    @pytest.mark.asyncio
    async def test_set_appearance(self, automation):
        """Should set simulator appearance mode."""
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(return_value=(b"", b""))
            mock_exec.return_value = mock_proc

            await automation._set_appearance("ABC-123", dark_mode=True)

            mock_exec.assert_called_once()
            call_args = mock_exec.call_args[0]
            assert "ui" in call_args
            assert "appearance" in call_args
            assert "dark" in call_args


class TestScreenshotSpec:
    """Test ScreenshotSpec dataclass."""

    def test_create_spec(self):
        """Should create screenshot specification."""
        spec = ScreenshotSpec(
            name="login",
            device_type=DeviceType.IPHONE_15_PRO,
            locale=Locale.EN_US,
            steps=["Launch app", "Navigate to login"],
        )

        assert spec.name == "login"
        assert spec.device_type == DeviceType.IPHONE_15_PRO
        assert spec.locale == Locale.EN_US
        assert len(spec.steps) == 2

    def test_create_spec_defaults(self):
        """Should use default values."""
        spec = ScreenshotSpec(
            name="main",
            device_type=DeviceType.IPHONE_15_PRO,
        )

        assert spec.locale == Locale.EN_US
        assert spec.light_mode is True
        assert len(spec.steps) == 0


class TestScreenshotSet:
    """Test ScreenshotSet dataclass."""

    def test_create_set(self):
        """Should create screenshot set."""
        screenshot_set = ScreenshotSet(
            device_type=DeviceType.IPHONE_15_PRO,
            locale=Locale.EN_US,
            screenshots=[Path("test1.png"), Path("test2.png")],
        )

        assert screenshot_set.device_type == DeviceType.IPHONE_15_PRO
        assert screenshot_set.locale == Locale.EN_US
        assert len(screenshot_set.screenshots) == 2

    def test_separate_light_dark_screenshots(self):
        """Should track light and dark mode screenshots separately."""
        screenshot_set = ScreenshotSet(
            device_type=DeviceType.IPHONE_15_PRO,
            locale=Locale.EN_US,
            light_mode_screenshots=[Path("light1.png")],
            dark_mode_screenshots=[Path("dark1.png")],
        )

        assert len(screenshot_set.light_mode_screenshots) == 1
        assert len(screenshot_set.dark_mode_screenshots) == 1


class TestScreenshotResult:
    """Test ScreenshotResult dataclass."""

    def test_result_success(self):
        """Should create successful result."""
        result = ScreenshotResult(
            success=True,
            screenshot_sets=[
                ScreenshotSet(
                    device_type=DeviceType.IPHONE_15_PRO,
                    locale=Locale.EN_US,
                    screenshots=[Path("test.png")],
                )
            ],
            total_screenshots=5,
        )

        assert result.success is True
        assert len(result.screenshot_sets) == 1
        assert result.total_screenshots == 5

    def test_result_failure(self):
        """Should create failed result."""
        result = ScreenshotResult(
            success=False,
            errors=["Failed to boot device", "App not installed"],
        )

        assert result.success is False
        assert len(result.errors) == 2


class TestScreenshotSize:
    """Test ScreenshotSize enum."""

    def test_screenshot_sizes(self):
        """Should have App Store screenshot sizes."""
        assert ScreenshotSize.IPHONE_67.value == "6.7-inch"
        assert ScreenshotSize.IPHONE_65.value == "6.5-inch"
        assert ScreenshotSize.IPAD_PRO_129.value == "12.9-inch"


class TestLocale:
    """Test Locale enum."""

    def test_locales(self):
        """Should have standard locales."""
        assert Locale.EN_US.value == "en-US"
        assert Locale.ES_ES.value == "es-ES"
        assert Locale.FR_FR.value == "fr-FR"
        assert Locale.DE_DE.value == "de-DE"
        assert Locale.JA_JP.value == "ja-JP"
        assert Locale.ZH_CN.value == "zh-CN"


class TestCreateStandardScreenshotSpecs:
    """Test create_standard_screenshot_specs function."""

    def test_create_default_specs(self):
        """Should create default screenshot specifications."""
        specs = create_standard_screenshot_specs("TestApp")

        # Should create specs for 2 devices x 1 locale x 3 screens = 6 specs
        assert len(specs) == 6

        # Verify device types
        device_types = {spec.device_type for spec in specs}
        assert DeviceType.IPHONE_15_PRO in device_types
        assert DeviceType.IPAD_PRO_13 in device_types

        # Verify locales
        locales = {spec.locale for spec in specs}
        assert Locale.EN_US in locales

    def test_create_custom_devices(self):
        """Should create specs for custom devices."""
        specs = create_standard_screenshot_specs(
            "TestApp",
            devices=[DeviceType.IPHONE_15],
        )

        assert len(specs) == 3  # 1 device x 1 locale x 3 screens
        assert all(spec.device_type == DeviceType.IPHONE_15 for spec in specs)

    def test_create_multiple_locales(self):
        """Should create specs for multiple locales."""
        specs = create_standard_screenshot_specs(
            "TestApp",
            devices=[DeviceType.IPHONE_15_PRO],
            locales=[Locale.EN_US, Locale.ES_ES],
        )

        assert len(specs) == 6  # 1 device x 2 locales x 3 screens

        locales = {spec.locale for spec in specs}
        assert Locale.EN_US in locales
        assert Locale.ES_ES in locales

    def test_spec_names(self):
        """Should create specs with standard screen names."""
        specs = create_standard_screenshot_specs("TestApp")

        names = {spec.name for spec in specs}
        assert "launch" in names
        assert "main_feature" in names
        assert "settings" in names
