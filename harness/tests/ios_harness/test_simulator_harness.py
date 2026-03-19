"""Tests for iOS Simulator automation."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from forge_harness.ios_harness.simulator_harness import (
    DeviceType,
    LaunchResult,
    SimulatorDevice,
    SimulatorHarness,
    SimulatorState,
)


@pytest.fixture
def harness():
    """Create SimulatorHarness instance."""
    return SimulatorHarness(default_device=DeviceType.IPHONE_15_PRO.value)


@pytest.fixture
def mock_simctl_list():
    """Mock simctl list output."""
    return {
        "devices": {
            "com.apple.CoreSimulator.SimRuntime.iOS-17-0": [
                {
                    "udid": "ABC-123",
                    "name": "iPhone 15 Pro",
                    "state": "Shutdown",
                    "isAvailable": True,
                },
                {
                    "udid": "DEF-456",
                    "name": "iPhone 15",
                    "state": "Booted",
                    "isAvailable": True,
                },
                {
                    "udid": "GHI-789",
                    "name": "iPhone SE (3rd generation)",
                    "state": "Shutdown",
                    "isAvailable": False,
                },
            ]
        }
    }


class TestSimulatorHarness:
    """Test SimulatorHarness class."""

    def test_init(self, harness):
        """Should initialize with default device."""
        assert harness.default_device == DeviceType.IPHONE_15_PRO.value

    def test_init_custom_device(self):
        """Should initialize with custom default device."""
        harness = SimulatorHarness(default_device="iPad Pro")
        assert harness.default_device == "iPad Pro"

    @pytest.mark.asyncio
    async def test_list_devices(self, harness, mock_simctl_list):
        """Should list available devices."""
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            # Mock process
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate = AsyncMock(
                return_value=(json.dumps(mock_simctl_list).encode(), b"")
            )
            mock_exec.return_value = mock_proc

            devices = await harness.list_devices()

            # Should only return available devices
            assert len(devices) == 2
            assert devices[0].name == "iPhone 15 Pro"
            assert devices[0].state == SimulatorState.SHUTDOWN
            assert devices[1].name == "iPhone 15"
            assert devices[1].state == SimulatorState.BOOTED

    @pytest.mark.asyncio
    async def test_list_devices_all(self, harness, mock_simctl_list):
        """Should list all devices when available_only=False."""
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate = AsyncMock(
                return_value=(json.dumps(mock_simctl_list).encode(), b"")
            )
            mock_exec.return_value = mock_proc

            devices = await harness.list_devices(available_only=False)

            # Should return all devices
            assert len(devices) == 3

    @pytest.mark.asyncio
    async def test_list_devices_filter_runtime(self, harness, mock_simctl_list):
        """Should filter devices by runtime."""
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate = AsyncMock(
                return_value=(json.dumps(mock_simctl_list).encode(), b"")
            )
            mock_exec.return_value = mock_proc

            devices = await harness.list_devices(runtime="iOS-17-0")

            assert len(devices) == 2

    @pytest.mark.asyncio
    async def test_get_device(self, harness, mock_simctl_list):
        """Should get specific device by name."""
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate = AsyncMock(
                return_value=(json.dumps(mock_simctl_list).encode(), b"")
            )
            mock_exec.return_value = mock_proc

            device = await harness.get_device("iPhone 15 Pro")

            assert device is not None
            assert device.name == "iPhone 15 Pro"
            assert device.udid == "ABC-123"

    @pytest.mark.asyncio
    async def test_get_device_not_found(self, harness):
        """Should return None for non-existent device."""
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate = AsyncMock(return_value=(b'{"devices":{}}', b""))
            mock_exec.return_value = mock_proc

            device = await harness.get_device("NonExistent Device")

            assert device is None

    @pytest.mark.asyncio
    async def test_boot_already_booted(self, harness, mock_simctl_list):
        """Should return device if already booted."""
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            # Set up mock to return booted device
            booted_simctl = mock_simctl_list.copy()
            booted_simctl["devices"]["com.apple.CoreSimulator.SimRuntime.iOS-17-0"][0]["state"] = (
                "Booted"
            )

            # Mock list_devices
            mock_proc_list = AsyncMock()
            mock_proc_list.returncode = 0
            mock_proc_list.communicate = AsyncMock(
                return_value=(json.dumps(booted_simctl).encode(), b"")
            )

            mock_exec.return_value = mock_proc_list

            device = await harness.boot("iPhone 15 Pro")

            assert device.state == SimulatorState.BOOTED

    @pytest.mark.asyncio
    async def test_boot_success(self, harness, mock_simctl_list):
        """Should boot shutdown device."""
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            # Mock list_devices call
            mock_proc_list = AsyncMock()
            mock_proc_list.returncode = 0
            mock_proc_list.communicate = AsyncMock(
                return_value=(json.dumps(mock_simctl_list).encode(), b"")
            )

            # Mock boot command
            mock_proc_boot = AsyncMock()
            mock_proc_boot.returncode = 0
            mock_proc_boot.communicate = AsyncMock(return_value=(b"", b""))

            # Mock bootstatus command
            mock_proc_status = AsyncMock()
            mock_proc_status.returncode = 0
            mock_proc_status.communicate = AsyncMock(return_value=(b"", b""))

            mock_exec.side_effect = [mock_proc_list, mock_proc_boot, mock_proc_status]

            device = await harness.boot("iPhone 15 Pro")

            assert device is not None

    @pytest.mark.asyncio
    async def test_shutdown(self, harness, mock_simctl_list):
        """Should shutdown booted device."""
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            # Mock list_devices
            mock_proc_list = AsyncMock()
            mock_proc_list.returncode = 0
            mock_proc_list.communicate = AsyncMock(
                return_value=(json.dumps(mock_simctl_list).encode(), b"")
            )

            # Mock shutdown command
            mock_proc_shutdown = AsyncMock()
            mock_proc_shutdown.returncode = 0
            mock_proc_shutdown.communicate = AsyncMock(return_value=(b"", b""))

            mock_exec.side_effect = [mock_proc_list, mock_proc_shutdown]

            success = await harness.shutdown("iPhone 15")

            assert success is True

    @pytest.mark.asyncio
    async def test_install_success(self, harness, mock_simctl_list, tmp_path):
        """Should install app on device."""
        # Create mock app bundle
        app_path = tmp_path / "Test.app"
        app_path.mkdir()
        info_plist = app_path / "Info.plist"

        # Create booted device mock
        booted_device = SimulatorDevice(
            udid="ABC-123",
            name="iPhone 15 Pro",
            state=SimulatorState.BOOTED,
            device_type="iPhone 15 Pro",
            runtime="iOS-17-0",
            is_available=True,
        )

        with (
            patch("asyncio.create_subprocess_exec") as mock_exec,
            patch.object(harness, "get_device", return_value=booted_device),
            patch.object(harness, "boot", return_value=booted_device),
            patch.object(harness, "_get_bundle_id", return_value="com.test.app"),
        ):
            # Mock install command
            mock_proc_install = AsyncMock()
            mock_proc_install.returncode = 0
            mock_proc_install.communicate = AsyncMock(return_value=(b"", b""))

            mock_exec.return_value = mock_proc_install

            result = await harness.install(app_path, "iPhone 15 Pro")

            assert result.success is True
            assert result.bundle_id == "com.test.app"

    @pytest.mark.asyncio
    async def test_launch_success(self, harness, mock_simctl_list):
        """Should launch app on device."""
        # Create booted device mock
        booted_device = SimulatorDevice(
            udid="ABC-123",
            name="iPhone 15 Pro",
            state=SimulatorState.BOOTED,
            device_type="iPhone 15 Pro",
            runtime="iOS-17-0",
            is_available=True,
        )

        with (
            patch("asyncio.create_subprocess_exec") as mock_exec,
            patch.object(harness, "get_device", return_value=booted_device),
            patch.object(harness, "boot", return_value=booted_device),
        ):
            # Mock launch command
            mock_proc_launch = AsyncMock()
            mock_proc_launch.returncode = 0
            mock_proc_launch.communicate = AsyncMock(return_value=(b"com.test.app: 12345", b""))

            mock_exec.return_value = mock_proc_launch

            result = await harness.launch("com.test.app", "iPhone 15 Pro", wait_for_ui=False)

            assert result.success is True
            assert result.bundle_id == "com.test.app"
            assert result.process_id == 12345

    @pytest.mark.asyncio
    async def test_launch_passes_simctl_child_environment(self, harness):
        """Should pass launch_env values via SIMCTL_CHILD_* variables."""
        booted_device = SimulatorDevice(
            udid="ABC-123",
            name="iPhone 15 Pro",
            state=SimulatorState.BOOTED,
            device_type="iPhone 15 Pro",
            runtime="iOS-17-0",
            is_available=True,
        )

        with (
            patch("asyncio.create_subprocess_exec") as mock_exec,
            patch.object(harness, "get_device", return_value=booted_device),
            patch.object(harness, "boot", return_value=booted_device),
        ):
            mock_proc_launch = AsyncMock()
            mock_proc_launch.returncode = 0
            mock_proc_launch.communicate = AsyncMock(return_value=(b"com.test.app: 12345", b""))
            mock_exec.return_value = mock_proc_launch

            await harness.launch(
                "com.test.app",
                "iPhone 15 Pro",
                wait_for_ui=False,
                launch_env={
                    "FORGE_RENDER_PROBE": "1",
                    "FORGE_SKIP_AUTO_CONNECT": "1",
                },
            )

            kwargs = mock_exec.call_args.kwargs
            env = kwargs["env"]
            assert env["SIMCTL_CHILD_FORGE_RENDER_PROBE"] == "1"
            assert env["SIMCTL_CHILD_FORGE_SKIP_AUTO_CONNECT"] == "1"

    @pytest.mark.asyncio
    async def test_launch_accepts_args_and_terminate_flag(self, harness):
        """Should include terminate flag and launch args in simctl launch command."""
        booted_device = SimulatorDevice(
            udid="ABC-123",
            name="iPhone 15 Pro",
            state=SimulatorState.BOOTED,
            device_type="iPhone 15 Pro",
            runtime="iOS-17-0",
            is_available=True,
        )

        with (
            patch("asyncio.create_subprocess_exec") as mock_exec,
            patch.object(harness, "get_device", return_value=booted_device),
            patch.object(harness, "boot", return_value=booted_device),
        ):
            mock_proc_launch = AsyncMock()
            mock_proc_launch.returncode = 0
            mock_proc_launch.communicate = AsyncMock(return_value=(b"com.test.app: 12345", b""))
            mock_exec.return_value = mock_proc_launch

            await harness.launch(
                "com.test.app",
                "iPhone 15 Pro",
                wait_for_ui=False,
                launch_args=["--probe", "enabled"],
                terminate_running_process=True,
            )

            args = list(mock_exec.call_args.args)
            assert "--terminate-running-process" in args
            assert args[-2:] == ["--probe", "enabled"]

    @pytest.mark.asyncio
    async def test_screenshot(self, harness, mock_simctl_list, tmp_path):
        """Should capture screenshot."""
        output_path = tmp_path / "screenshot.png"

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            # Mock list_devices
            mock_proc_list = AsyncMock()
            mock_proc_list.returncode = 0
            mock_proc_list.communicate = AsyncMock(
                return_value=(json.dumps(mock_simctl_list).encode(), b"")
            )

            # Mock screenshot command
            mock_proc_screenshot = AsyncMock()
            mock_proc_screenshot.returncode = 0
            mock_proc_screenshot.communicate = AsyncMock(return_value=(b"", b""))

            mock_exec.side_effect = [mock_proc_list, mock_proc_screenshot]

            # Create fake screenshot file
            output_path.touch()

            success = await harness.screenshot(output_path, "iPhone 15 Pro")

            assert success is True

    @pytest.mark.asyncio
    async def test_uninstall(self, harness, mock_simctl_list):
        """Should uninstall app from device."""
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            # Mock list_devices
            mock_proc_list = AsyncMock()
            mock_proc_list.returncode = 0
            mock_proc_list.communicate = AsyncMock(
                return_value=(json.dumps(mock_simctl_list).encode(), b"")
            )

            # Mock uninstall command
            mock_proc_uninstall = AsyncMock()
            mock_proc_uninstall.returncode = 0
            mock_proc_uninstall.communicate = AsyncMock(return_value=(b"", b""))

            mock_exec.side_effect = [mock_proc_list, mock_proc_uninstall]

            success = await harness.uninstall("com.test.app", "iPhone 15 Pro")

            assert success is True

    @pytest.mark.asyncio
    async def test_get_logs_filters_known_noise(self, harness):
        """Should filter simulator keyboard/haptics noise by default."""
        booted_device = SimulatorDevice(
            udid="ABC-123",
            name="iPhone 15 Pro",
            state=SimulatorState.BOOTED,
            device_type="iPhone 15 Pro",
            runtime="iOS-17-0",
            is_available=True,
        )

        noisy = "CHHapticPattern.mm:487 Failed to read pattern library data"
        noisy_uid = "getpwuid_r did not find a match for uid 501"
        useful = "[forge-terminal] transport_state state=connected"
        payload = f"{noisy}\n{noisy_uid}\n{useful}\n".encode()

        with (
            patch("asyncio.create_subprocess_exec") as mock_exec,
            patch.object(harness, "get_device", return_value=booted_device),
        ):
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate = AsyncMock(return_value=(payload, b""))
            mock_exec.return_value = mock_proc

            logs = await harness.get_logs("com.test.app", "iPhone 15 Pro")

            assert logs == [useful]

    @pytest.mark.asyncio
    async def test_get_logs_can_return_noise_when_requested(self, harness):
        """Should keep all lines when exclude_noise=False."""
        booted_device = SimulatorDevice(
            udid="ABC-123",
            name="iPhone 15 Pro",
            state=SimulatorState.BOOTED,
            device_type="iPhone 15 Pro",
            runtime="iOS-17-0",
            is_available=True,
        )

        noisy = "_UIKBFeedbackGenerator: Engine is not running. Can't play feedback."
        useful = "[forge-terminal] relay_ready relay=ws://127.0.0.1:8080/ssh"
        payload = f"{noisy}\n{useful}\n".encode()

        with (
            patch("asyncio.create_subprocess_exec") as mock_exec,
            patch.object(harness, "get_device", return_value=booted_device),
        ):
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate = AsyncMock(return_value=(payload, b""))
            mock_exec.return_value = mock_proc

            logs = await harness.get_logs(
                "com.test.app",
                "iPhone 15 Pro",
                exclude_noise=False,
            )

            assert logs == [noisy, useful]


class TestDeviceType:
    """Test DeviceType enum."""

    def test_device_types(self):
        """Should have standard device types."""
        assert DeviceType.IPHONE_15_PRO.value == "iPhone 15 Pro"
        assert DeviceType.IPHONE_15.value == "iPhone 15"
        assert DeviceType.IPAD_PRO_13.value == "iPad Pro (13-inch) (M4)"


class TestSimulatorDevice:
    """Test SimulatorDevice dataclass."""

    def test_create_device(self):
        """Should create simulator device."""
        device = SimulatorDevice(
            udid="ABC-123",
            name="iPhone 15 Pro",
            state=SimulatorState.BOOTED,
            device_type="iPhone 15 Pro",
            runtime="iOS 17.0",
            is_available=True,
        )

        assert device.udid == "ABC-123"
        assert device.name == "iPhone 15 Pro"
        assert device.state == SimulatorState.BOOTED


class TestLaunchResult:
    """Test LaunchResult dataclass."""

    def test_launch_result_success(self):
        """Should create successful launch result."""
        result = LaunchResult(
            success=True,
            bundle_id="com.test.app",
            process_id=12345,
        )

        assert result.success is True
        assert result.bundle_id == "com.test.app"
        assert result.process_id == 12345
        assert result.error is None

    def test_launch_result_failure(self):
        """Should create failed launch result."""
        result = LaunchResult(
            success=False,
            bundle_id="com.test.app",
            error="App not installed",
        )

        assert result.success is False
        assert result.error == "App not installed"
