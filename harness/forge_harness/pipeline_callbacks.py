"""
Pipeline Callbacks for Real-Time Status Updates
================================================

Provides callback interfaces for observing pipeline execution.

Usage:
    from forge_harness.pipeline_callbacks import (
        PipelineEvent,
        PipelineCallback,
        ConsolePipelineCallback,
        FilePipelineCallback,
    )

    # Create callback
    console_cb = ConsolePipelineCallback()
    file_cb = FilePipelineCallback(Path("pipeline.log"))

    # Register with orchestrator
    orchestrator.register_callback(console_cb)
    orchestrator.register_callback(file_cb)

    # Execute - callbacks receive events
    await orchestrator.execute(pipeline)
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .logging_config import get_logger

logger = get_logger(__name__)


# =============================================================================
# Event Types
# =============================================================================


class EventType:
    """Pipeline event type constants."""

    PIPELINE_STARTED = "pipeline_started"
    PIPELINE_COMPLETED = "pipeline_completed"
    PIPELINE_FAILED = "pipeline_failed"
    STEP_STARTED = "step_started"
    STEP_COMPLETED = "step_completed"
    STEP_FAILED = "step_failed"
    STEP_SKIPPED = "step_skipped"
    STEP_RETRYING = "step_retrying"
    HUMAN_GATE_WAITING = "human_gate_waiting"
    HUMAN_GATE_RESOLVED = "human_gate_resolved"
    CHECKPOINT_SAVED = "checkpoint_saved"


@dataclass
class PipelineEvent:
    """Event emitted during pipeline execution.

    Attributes:
        event_type: Type of event (see EventType constants)
        pipeline_name: Name of the pipeline
        step_name: Name of the step (if applicable)
        timestamp: When the event occurred
        data: Additional event data
    """

    event_type: str
    pipeline_name: str
    step_name: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to serializable dict."""
        return {
            "event_type": self.event_type,
            "pipeline_name": self.pipeline_name,
            "step_name": self.step_name,
            "timestamp": self.timestamp.isoformat(),
            "data": self.data,
        }


# =============================================================================
# Callback Protocol
# =============================================================================


@runtime_checkable
class PipelineCallback(Protocol):
    """Protocol for pipeline event callbacks.

    Implement this protocol to receive events during pipeline execution.
    """

    async def on_event(self, event: PipelineEvent) -> None:
        """Handle a pipeline event.

        Args:
            event: The pipeline event
        """
        ...


# =============================================================================
# Console Callback
# =============================================================================


class ConsolePipelineCallback:
    """Callback that prints pipeline events to console.

    Provides colorized, formatted output for terminal display.
    """

    def __init__(self, use_colors: bool = True, show_timestamps: bool = True):
        """Initialize console callback.

        Args:
            use_colors: Whether to use ANSI colors
            show_timestamps: Whether to show timestamps
        """
        self.use_colors = use_colors
        self.show_timestamps = show_timestamps

    async def on_event(self, event: PipelineEvent) -> None:
        """Print event to console.

        Args:
            event: The pipeline event
        """
        message = self._format_event(event)
        print(message)

    def _format_event(self, event: PipelineEvent) -> str:
        """Format event for display.

        Args:
            event: The pipeline event

        Returns:
            Formatted string
        """
        # Build timestamp prefix
        timestamp = ""
        if self.show_timestamps:
            timestamp = f"[{event.timestamp.strftime('%H:%M:%S')}] "

        # Get icon and color based on event type
        icon, color = self._get_event_style(event.event_type)

        # Build the message
        if event.step_name:
            location = f"{event.pipeline_name}/{event.step_name}"
        else:
            location = event.pipeline_name

        message = f"{icon} {location}: {event.event_type}"

        # Add any extra data info
        if event.data:
            if "error" in event.data:
                message += f" - {event.data['error']}"
            elif "duration" in event.data:
                message += f" ({event.data['duration']:.1f}s)"

        return f"{timestamp}{self._colorize(message, color)}"

    def _get_event_style(self, event_type: str) -> tuple[str, str]:
        """Get icon and color for event type.

        Args:
            event_type: The event type string

        Returns:
            Tuple of (icon, color)
        """
        styles = {
            EventType.PIPELINE_STARTED: ("▶", "blue"),
            EventType.PIPELINE_COMPLETED: ("✓", "green"),
            EventType.PIPELINE_FAILED: ("✗", "red"),
            EventType.STEP_STARTED: ("→", "blue"),
            EventType.STEP_COMPLETED: ("✓", "green"),
            EventType.STEP_FAILED: ("✗", "red"),
            EventType.STEP_SKIPPED: ("○", "yellow"),
            EventType.STEP_RETRYING: ("↻", "yellow"),
            EventType.HUMAN_GATE_WAITING: ("⏳", "magenta"),
            EventType.HUMAN_GATE_RESOLVED: ("✓", "magenta"),
            EventType.CHECKPOINT_SAVED: ("💾", "blue"),
        }
        return styles.get(event_type, ("•", "reset"))

    def _colorize(self, text: str, color: str) -> str:
        """Apply ANSI color to text.

        Args:
            text: Text to colorize
            color: Color name (red, green, blue, yellow, magenta)

        Returns:
            Colorized text (or original if colors disabled)
        """
        if not self.use_colors:
            return text

        colors = {
            "red": "\033[91m",
            "green": "\033[92m",
            "yellow": "\033[93m",
            "blue": "\033[94m",
            "magenta": "\033[95m",
            "reset": "\033[0m",
        }

        return f"{colors.get(color, '')}{text}{colors['reset']}"


# =============================================================================
# File Callback
# =============================================================================


class FilePipelineCallback:
    """Callback that writes pipeline events to a file.

    Writes JSON-formatted events for later analysis.
    """

    def __init__(self, log_path: Path, append: bool = True):
        """Initialize file callback.

        Args:
            log_path: Path to log file
            append: Whether to append (True) or overwrite (False)
        """
        self.log_path = Path(log_path)
        self.append = append

        # Ensure parent directory exists
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    async def on_event(self, event: PipelineEvent) -> None:
        """Write event to file.

        Args:
            event: The pipeline event
        """
        import json

        try:
            # Convert event to JSON line
            event_json = json.dumps(event.to_dict())

            # Determine write mode
            mode = "a" if self.append else "w"

            # Write to file
            with open(self.log_path, mode) as f:
                f.write(event_json + "\n")

        except OSError as e:
            logger.warning(f"Failed to write event to file {self.log_path}: {e}")


# =============================================================================
# Aggregating Callback
# =============================================================================


class AggregatingCallback:
    """Callback that collects events for later retrieval.

    Useful for testing and programmatic access to events.
    """

    def __init__(self, max_events: int = 1000):
        """Initialize aggregating callback.

        Args:
            max_events: Maximum events to store (oldest dropped when exceeded)
        """
        self.max_events = max_events
        self.events: list[PipelineEvent] = []

    async def on_event(self, event: PipelineEvent) -> None:
        """Store event.

        Args:
            event: The pipeline event
        """
        self.events.append(event)

        # Drop oldest events if max exceeded
        while len(self.events) > self.max_events:
            self.events.pop(0)

    def get_events(self, event_type: str | None = None) -> list[PipelineEvent]:
        """Get stored events, optionally filtered by type.

        Args:
            event_type: Optional event type to filter by

        Returns:
            List of events
        """
        if event_type is None:
            return list(self.events)
        return [e for e in self.events if e.event_type == event_type]

    def clear(self) -> None:
        """Clear all stored events."""
        self.events.clear()


# =============================================================================
# Callback Manager
# =============================================================================


class CallbackManager:
    """Manages multiple callbacks for pipeline execution.

    Dispatches events to all registered callbacks.
    """

    def __init__(self):
        """Initialize callback manager."""
        self._callbacks: list[PipelineCallback] = []

    def register(self, callback: PipelineCallback) -> None:
        """Register a callback.

        Args:
            callback: Callback to register
        """
        if callback not in self._callbacks:
            self._callbacks.append(callback)

    def unregister(self, callback: PipelineCallback) -> None:
        """Unregister a callback.

        Args:
            callback: Callback to remove
        """
        if callback in self._callbacks:
            self._callbacks.remove(callback)

    async def emit(self, event: PipelineEvent) -> None:
        """Emit event to all callbacks.

        Args:
            event: Event to emit
        """
        for callback in self._callbacks:
            try:
                await callback.on_event(event)
            except Exception as e:
                logger.warning(
                    f"Callback {type(callback).__name__} failed: {e}",
                    exc_info=True,
                )

    @property
    def callback_count(self) -> int:
        """Number of registered callbacks."""
        return len(self._callbacks)


# =============================================================================
# Factory Functions
# =============================================================================


def create_console_callback(
    use_colors: bool = True,
    show_timestamps: bool = True,
) -> ConsolePipelineCallback:
    """Create a console callback.

    Args:
        use_colors: Whether to use ANSI colors
        show_timestamps: Whether to show timestamps

    Returns:
        Configured ConsolePipelineCallback
    """
    return ConsolePipelineCallback(
        use_colors=use_colors,
        show_timestamps=show_timestamps,
    )


def create_file_callback(
    log_path: Path | str,
    append: bool = True,
) -> FilePipelineCallback:
    """Create a file callback.

    Args:
        log_path: Path to log file
        append: Whether to append to existing file

    Returns:
        Configured FilePipelineCallback
    """
    return FilePipelineCallback(
        log_path=Path(log_path),
        append=append,
    )
