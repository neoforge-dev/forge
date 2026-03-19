"""
Simulator Harness Module
=========================

iOS Simulator automation for testing and validation.

Features:
- Boot/shutdown simulators
- Install/uninstall apps
- Launch apps and capture UI state
- Screenshot automation
- Accessibility validation
- Log collection
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class SimulatorState(Enum):
    """Simulator power states."""

    SHUTDOWN = "Shutdown"
    BOOTED = "Booted"
    BOOTING = "Booting"
    SHUTTING_DOWN = "Shutting Down"


class DeviceType(Enum):
    """Common iOS device types."""

    IPHONE_15_PRO = "iPhone 15 Pro"
    IPHONE_15 = "iPhone 15"
    IPHONE_SE_3RD = "iPhone SE (3rd generation)"
    IPAD_PRO_13 = "iPad Pro (13-inch) (M4)"
    IPAD_AIR_11 = "iPad Air 11-inch (M2)"


@dataclass
class SimulatorDevice:
    """iOS Simulator device info."""

    udid: str
    name: str
    state: SimulatorState
    device_type: str
    runtime: str
    is_available: bool


@dataclass
class LaunchResult:
    """Result of app launch."""

    success: bool
    bundle_id: str
    process_id: int | None = None
    error: str | None = None
    screenshots: list[Path] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)


@dataclass
class InstallResult:
    """Result of app installation."""

    success: bool
    bundle_id: str
    app_path: Path
    error: str | None = None


class SimulatorHarness:
    """iOS Simulator automation harness."""

    _NOISE_PATTERNS = (
        re.compile(r"CHHapticPattern\.mm", re.IGNORECASE),
        re.compile(r"_UIKBFeedbackGenerator", re.IGNORECASE),
        re.compile(r"UIKeyboardLayoutStar implements focusItemsInRect", re.IGNORECASE),
        re.compile(r"RTIInputSystemClient .*valid sessionID", re.IGNORECASE),
        re.compile(r"Reporter disconnected(\.| or already stopped\.)", re.IGNORECASE),
        re.compile(r"hapticpatternlibrary\.plist", re.IGNORECASE),
        re.compile(r"getpwuid_r did not find a match for uid", re.IGNORECASE),
        re.compile(r"NSMapGet\(NSMapTable .* map table argument is NULL", re.IGNORECASE),
        re.compile(r"MGIsDeviceOfType is not supported on this platform", re.IGNORECASE),
    )

    def __init__(self, default_device: str | None = None):
        """
        Initialize simulator harness.

        Args:
            default_device: Default device name (e.g., "iPhone 15 Pro")
        """
        self.default_device = default_device or DeviceType.IPHONE_15_PRO.value

    async def list_devices(
        self,
        runtime: str | None = None,
        available_only: bool = True,
    ) -> list[SimulatorDevice]:
        """
        List available iOS simulators.

        Args:
            runtime: Filter by runtime (e.g., "iOS 17.0")
            available_only: Only return available devices

        Returns:
            List of simulator devices
        """
        cmd = ["xcrun", "simctl", "list", "devices", "-j"]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            raise RuntimeError(f"Failed to list devices: {stderr.decode()}")

        data = json.loads(stdout.decode())
        devices = []

        for runtime_name, device_list in data.get("devices", {}).items():
            # Filter by runtime if specified
            if runtime and runtime not in runtime_name:
                continue

            for device in device_list:
                if available_only and not device.get("isAvailable", False):
                    continue

                devices.append(
                    SimulatorDevice(
                        udid=device["udid"],
                        name=device["name"],
                        state=SimulatorState(device["state"]),
                        device_type=device["name"],
                        runtime=runtime_name,
                        is_available=device.get("isAvailable", False),
                    )
                )

        return devices

    async def get_device(self, device_name: str | None = None) -> SimulatorDevice | None:
        """
        Get specific device by name.

        Args:
            device_name: Device name (defaults to default_device)

        Returns:
            SimulatorDevice or None if not found
        """
        devices = await self.list_devices()
        target_name = device_name or self.default_device

        for device in devices:
            if device.name == target_name:
                return device

        return None

    async def boot(self, device_name: str | None = None) -> SimulatorDevice:
        """
        Boot simulator.

        Args:
            device_name: Device name (defaults to default_device)

        Returns:
            Booted simulator device

        Raises:
            RuntimeError: If boot fails
        """
        device = await self.get_device(device_name)
        if not device:
            raise RuntimeError(f"Device not found: {device_name or self.default_device}")

        # Already booted?
        if device.state == SimulatorState.BOOTED:
            return device

        # Boot the device
        cmd = ["xcrun", "simctl", "boot", device.udid]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            error = stderr.decode()
            # Already booted is not an error
            if "Unable to boot device in current state: Booted" not in error:
                raise RuntimeError(f"Failed to boot device: {error}")

        # Wait for boot to complete
        await self._wait_for_boot(device.udid)

        # Return updated device state
        device.state = SimulatorState.BOOTED
        return device

    async def shutdown(self, device_name: str | None = None) -> bool:
        """
        Shutdown simulator.

        Args:
            device_name: Device name (defaults to default_device)

        Returns:
            True if successful
        """
        device = await self.get_device(device_name)
        if not device:
            return False

        if device.state == SimulatorState.SHUTDOWN:
            return True

        cmd = ["xcrun", "simctl", "shutdown", device.udid]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

        return proc.returncode == 0

    async def install(self, app_path: Path, device_name: str | None = None) -> InstallResult:
        """
        Install app on simulator.

        Args:
            app_path: Path to .app bundle
            device_name: Device name (defaults to default_device)

        Returns:
            InstallResult with bundle ID and status
        """
        device = await self.get_device(device_name)
        if not device:
            return InstallResult(
                success=False,
                bundle_id="",
                app_path=app_path,
                error=f"Device not found: {device_name or self.default_device}",
            )

        # Ensure device is booted
        if device.state != SimulatorState.BOOTED:
            await self.boot(device_name)

        # Get bundle ID from Info.plist
        bundle_id = await self._get_bundle_id(app_path)
        if not bundle_id:
            return InstallResult(
                success=False,
                bundle_id="",
                app_path=app_path,
                error="Failed to read bundle ID from Info.plist",
            )

        # Install app
        cmd = ["xcrun", "simctl", "install", device.udid, str(app_path)]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            return InstallResult(
                success=False,
                bundle_id=bundle_id,
                app_path=app_path,
                error=stderr.decode(),
            )

        return InstallResult(
            success=True,
            bundle_id=bundle_id,
            app_path=app_path,
        )

    async def uninstall(self, bundle_id: str, device_name: str | None = None) -> bool:
        """
        Uninstall app from simulator.

        Args:
            bundle_id: App bundle identifier
            device_name: Device name (defaults to default_device)

        Returns:
            True if successful
        """
        device = await self.get_device(device_name)
        if not device:
            return False

        cmd = ["xcrun", "simctl", "uninstall", device.udid, bundle_id]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

        return proc.returncode == 0

    async def launch(
        self,
        bundle_id: str,
        device_name: str | None = None,
        wait_for_ui: bool = True,
        launch_args: list[str] | None = None,
        launch_env: Mapping[str, str] | None = None,
        terminate_running_process: bool = False,
    ) -> LaunchResult:
        """
        Launch app on simulator.

        Args:
            bundle_id: App bundle identifier
            device_name: Device name (defaults to default_device)
            wait_for_ui: Wait for UI to be ready
            launch_args: Optional launch arguments passed to the app process
            launch_env: Optional app environment variables (via SIMCTL_CHILD_*)
            terminate_running_process: Terminate existing process before launch

        Returns:
            LaunchResult with process ID and screenshots
        """
        device = await self.get_device(device_name)
        if not device:
            return LaunchResult(
                success=False,
                bundle_id=bundle_id,
                error=f"Device not found: {device_name or self.default_device}",
            )

        # Ensure device is booted
        if device.state != SimulatorState.BOOTED:
            await self.boot(device_name)

        # Launch app
        cmd = ["xcrun", "simctl", "launch"]
        if terminate_running_process:
            cmd.append("--terminate-running-process")
        cmd.extend([device.udid, bundle_id])
        if launch_args:
            cmd.extend(launch_args)

        child_env = os.environ.copy()
        if launch_env:
            for key, value in launch_env.items():
                child_env[f"SIMCTL_CHILD_{key}"] = value

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            env=child_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            return LaunchResult(
                success=False,
                bundle_id=bundle_id,
                error=stderr.decode(),
            )

        # Extract process ID from output
        output = stdout.decode().strip()
        process_id = None
        if ":" in output:
            try:
                process_id = int(output.split(":")[-1].strip())
            except ValueError:
                pass

        result = LaunchResult(
            success=True,
            bundle_id=bundle_id,
            process_id=process_id,
        )

        # Wait for UI if requested
        if wait_for_ui:
            await asyncio.sleep(2)  # Give UI time to render

        return result

    async def screenshot(
        self,
        output_path: Path,
        device_name: str | None = None,
    ) -> bool:
        """
        Take screenshot of simulator.

        Args:
            output_path: Where to save screenshot (PNG)
            device_name: Device name (defaults to default_device)

        Returns:
            True if successful
        """
        device = await self.get_device(device_name)
        if not device:
            return False

        output_path.parent.mkdir(parents=True, exist_ok=True)

        cmd = ["xcrun", "simctl", "io", device.udid, "screenshot", str(output_path)]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

        return proc.returncode == 0 and output_path.exists()

    async def get_logs(
        self,
        bundle_id: str,
        device_name: str | None = None,
        since: str = "1m",
        include_info: bool = True,
        exclude_noise: bool = True,
    ) -> list[str]:
        """
        Get app logs from simulator.

        Args:
            bundle_id: App bundle identifier
            device_name: Device name (defaults to default_device)
            since: Time period (e.g., "1m", "5m", "1h")
            include_info: Include info-level entries in output
            exclude_noise: Filter known simulator framework noise lines

        Returns:
            List of log lines
        """
        device = await self.get_device(device_name)
        if not device:
            return []

        cmd = [
            "xcrun",
            "simctl",
            "spawn",
            device.udid,
            "log",
            "show",
            "--predicate",
            f'processImagePath CONTAINS "{bundle_id}"',
            "--last",
            since,
            "--style",
            "compact",
        ]
        if include_info:
            cmd.append("--info")

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            return []

        lines = [line for line in stdout.decode().strip().split("\n") if line]
        if not exclude_noise:
            return lines
        return [line for line in lines if not self._is_noise_line(line)]

    @classmethod
    def _is_noise_line(cls, line: str) -> bool:
        return any(pattern.search(line) for pattern in cls._NOISE_PATTERNS)

    async def _wait_for_boot(self, udid: str, timeout: int = 60) -> None:
        """Wait for device to finish booting."""
        for _ in range(timeout):
            cmd = ["xcrun", "simctl", "bootstatus", udid]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()

            if proc.returncode == 0:
                return

            await asyncio.sleep(1)

        raise TimeoutError(f"Device failed to boot within {timeout} seconds")

    async def _get_bundle_id(self, app_path: Path) -> str | None:
        """Extract bundle ID from Info.plist."""
        info_plist = app_path / "Info.plist"
        if not info_plist.exists():
            return None

        cmd = ["/usr/libexec/PlistBuddy", "-c", "Print :CFBundleIdentifier", str(info_plist)]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            return None

        return stdout.decode().strip()
