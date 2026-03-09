"""
Tests for Pipeline Callbacks
============================

Tests for forge_harness.pipeline_callbacks module.
"""

import pytest


class TestPipelineEvent:
    """Tests for PipelineEvent dataclass."""

    def test_pipeline_event(self):
        """Test PipelineEvent creation and fields."""
        from forge_harness.pipeline_callbacks import EventType, PipelineEvent

        event = PipelineEvent(
            event_type=EventType.STEP_STARTED,
            pipeline_name="test_pipeline",
            step_name="step1",
        )
        assert event.event_type == EventType.STEP_STARTED
        assert event.pipeline_name == "test_pipeline"
        assert event.step_name == "step1"
        assert event.timestamp is not None
        assert event.data == {}

    def test_pipeline_event_with_data(self):
        """Test PipelineEvent with custom data."""
        from forge_harness.pipeline_callbacks import EventType, PipelineEvent

        event = PipelineEvent(
            event_type=EventType.STEP_COMPLETED,
            pipeline_name="test_pipeline",
            step_name="step1",
            data={"duration": 1.5, "outputs": ["result"]},
        )
        assert event.data["duration"] == 1.5
        assert event.data["outputs"] == ["result"]

    def test_event_type_constants(self):
        """Test EventType constants exist."""
        from forge_harness.pipeline_callbacks import EventType

        # Pipeline level events
        assert hasattr(EventType, "PIPELINE_STARTED")
        assert hasattr(EventType, "PIPELINE_COMPLETED")
        assert hasattr(EventType, "PIPELINE_FAILED")

        # Step level events
        assert hasattr(EventType, "STEP_STARTED")
        assert hasattr(EventType, "STEP_COMPLETED")
        assert hasattr(EventType, "STEP_FAILED")
        assert hasattr(EventType, "STEP_SKIPPED")
        assert hasattr(EventType, "STEP_RETRYING")

        # Human gate events
        assert hasattr(EventType, "HUMAN_GATE_WAITING")
        assert hasattr(EventType, "HUMAN_GATE_RESOLVED")

        # Checkpoint events
        assert hasattr(EventType, "CHECKPOINT_SAVED")

    def test_pipeline_event_to_dict(self):
        """Test PipelineEvent.to_dict serialization."""
        from forge_harness.pipeline_callbacks import EventType, PipelineEvent

        event = PipelineEvent(
            event_type=EventType.STEP_COMPLETED,
            pipeline_name="test_pipeline",
            step_name="step1",
            data={"result": "success"},
        )
        d = event.to_dict()

        assert d["event_type"] == EventType.STEP_COMPLETED
        assert d["pipeline_name"] == "test_pipeline"
        assert d["step_name"] == "step1"
        assert d["data"] == {"result": "success"}
        assert "timestamp" in d
        # Timestamp should be ISO format string
        assert isinstance(d["timestamp"], str)
        assert "T" in d["timestamp"]  # ISO format contains T


class TestConsolePipelineCallback:
    """Tests for ConsolePipelineCallback."""

    @pytest.mark.asyncio
    async def test_console_callback(self, capsys):
        """Test console callback prints events."""
        from forge_harness.pipeline_callbacks import (
            ConsolePipelineCallback,
            EventType,
            PipelineEvent,
        )

        callback = ConsolePipelineCallback(use_colors=False, show_timestamps=False)
        event = PipelineEvent(
            event_type=EventType.STEP_STARTED,
            pipeline_name="test_pipeline",
            step_name="step1",
        )
        await callback.on_event(event)

        captured = capsys.readouterr()
        assert "test_pipeline" in captured.out
        assert "step1" in captured.out
        assert "step_started" in captured.out

    @pytest.mark.asyncio
    async def test_console_callback_colors(self, capsys):
        """Test console callback with colors."""
        from forge_harness.pipeline_callbacks import (
            ConsolePipelineCallback,
            EventType,
            PipelineEvent,
        )

        callback = ConsolePipelineCallback(use_colors=True, show_timestamps=False)
        event = PipelineEvent(
            event_type=EventType.STEP_COMPLETED,
            pipeline_name="test",
        )
        await callback.on_event(event)

        captured = capsys.readouterr()
        # Should contain ANSI color codes for green (completed = green)
        assert "\033[92m" in captured.out or "test" in captured.out

    @pytest.mark.asyncio
    async def test_console_callback_no_colors(self, capsys):
        """Test console callback without colors."""
        from forge_harness.pipeline_callbacks import (
            ConsolePipelineCallback,
            EventType,
            PipelineEvent,
        )

        callback = ConsolePipelineCallback(use_colors=False, show_timestamps=False)
        event = PipelineEvent(
            event_type=EventType.STEP_COMPLETED,
            pipeline_name="test",
        )
        await callback.on_event(event)

        captured = capsys.readouterr()
        # Should NOT contain ANSI color codes
        assert "\033[" not in captured.out
        assert "test" in captured.out

    @pytest.mark.asyncio
    async def test_console_callback_timestamps(self, capsys):
        """Test console callback with timestamps."""
        from forge_harness.pipeline_callbacks import (
            ConsolePipelineCallback,
            EventType,
            PipelineEvent,
        )

        callback = ConsolePipelineCallback(use_colors=False, show_timestamps=True)
        event = PipelineEvent(
            event_type=EventType.STEP_STARTED,
            pipeline_name="test",
        )
        await callback.on_event(event)

        captured = capsys.readouterr()
        # Should contain timestamp format [HH:MM:SS]
        assert "[" in captured.out
        assert "]" in captured.out


class TestFilePipelineCallback:
    """Tests for FilePipelineCallback."""

    @pytest.mark.asyncio
    async def test_file_callback(self, tmp_path):
        """Test file callback writes events."""
        import json

        from forge_harness.pipeline_callbacks import (
            EventType,
            FilePipelineCallback,
            PipelineEvent,
        )

        log_file = tmp_path / "pipeline.log"
        callback = FilePipelineCallback(log_file)

        event = PipelineEvent(
            event_type=EventType.STEP_STARTED,
            pipeline_name="test_pipeline",
            step_name="step1",
        )
        await callback.on_event(event)

        # Verify file was written
        assert log_file.exists()
        content = log_file.read_text()
        data = json.loads(content.strip())
        assert data["event_type"] == EventType.STEP_STARTED
        assert data["pipeline_name"] == "test_pipeline"
        assert data["step_name"] == "step1"

    @pytest.mark.asyncio
    async def test_file_callback_append(self, tmp_path):
        """Test file callback in append mode."""
        import json

        from forge_harness.pipeline_callbacks import (
            EventType,
            FilePipelineCallback,
            PipelineEvent,
        )

        log_file = tmp_path / "pipeline.log"
        callback = FilePipelineCallback(log_file, append=True)

        # Write first event
        event1 = PipelineEvent(
            event_type=EventType.STEP_STARTED,
            pipeline_name="test_pipeline",
            step_name="step1",
        )
        await callback.on_event(event1)

        # Write second event
        event2 = PipelineEvent(
            event_type=EventType.STEP_COMPLETED,
            pipeline_name="test_pipeline",
            step_name="step1",
        )
        await callback.on_event(event2)

        # Verify both events are in file
        content = log_file.read_text()
        lines = content.strip().split("\n")
        assert len(lines) == 2

        data1 = json.loads(lines[0])
        data2 = json.loads(lines[1])
        assert data1["event_type"] == EventType.STEP_STARTED
        assert data2["event_type"] == EventType.STEP_COMPLETED

    @pytest.mark.asyncio
    async def test_file_callback_overwrite(self, tmp_path):
        """Test file callback in overwrite mode."""
        import json

        from forge_harness.pipeline_callbacks import (
            EventType,
            FilePipelineCallback,
            PipelineEvent,
        )

        log_file = tmp_path / "pipeline.log"
        callback = FilePipelineCallback(log_file, append=False)

        # Write first event
        event1 = PipelineEvent(
            event_type=EventType.STEP_STARTED,
            pipeline_name="test_pipeline",
            step_name="step1",
        )
        await callback.on_event(event1)

        # Write second event (should overwrite)
        event2 = PipelineEvent(
            event_type=EventType.STEP_COMPLETED,
            pipeline_name="test_pipeline",
            step_name="step1",
        )
        await callback.on_event(event2)

        # Verify only second event is in file
        content = log_file.read_text()
        lines = content.strip().split("\n")
        assert len(lines) == 1

        data = json.loads(lines[0])
        assert data["event_type"] == EventType.STEP_COMPLETED

    def test_file_callback_creates_directory(self, tmp_path):
        """Test file callback creates parent directory."""
        from forge_harness.pipeline_callbacks import FilePipelineCallback

        # Path with nested directory that doesn't exist
        log_file = tmp_path / "nested" / "dir" / "pipeline.log"
        callback = FilePipelineCallback(log_file)

        # Directory should be created on init
        assert log_file.parent.exists()


class TestAggregatingCallback:
    """Tests for AggregatingCallback."""

    @pytest.mark.asyncio
    async def test_aggregating_callback(self):
        """Test aggregating callback stores events."""
        from forge_harness.pipeline_callbacks import (
            AggregatingCallback,
            EventType,
            PipelineEvent,
        )

        callback = AggregatingCallback()

        event = PipelineEvent(
            event_type=EventType.STEP_STARTED,
            pipeline_name="test_pipeline",
            step_name="step1",
        )
        await callback.on_event(event)

        events = callback.get_events()
        assert len(events) == 1
        assert events[0].event_type == EventType.STEP_STARTED
        assert events[0].pipeline_name == "test_pipeline"

    @pytest.mark.asyncio
    async def test_aggregating_callback_max_events(self):
        """Test aggregating callback respects max_events."""
        from forge_harness.pipeline_callbacks import (
            AggregatingCallback,
            EventType,
            PipelineEvent,
        )

        callback = AggregatingCallback(max_events=3)

        # Add 5 events
        for i in range(5):
            event = PipelineEvent(
                event_type=EventType.STEP_STARTED,
                pipeline_name="test_pipeline",
                step_name=f"step{i}",
            )
            await callback.on_event(event)

        events = callback.get_events()
        assert len(events) == 3
        # Should have steps 2, 3, 4 (oldest dropped)
        assert events[0].step_name == "step2"
        assert events[1].step_name == "step3"
        assert events[2].step_name == "step4"

    @pytest.mark.asyncio
    async def test_aggregating_callback_filter(self):
        """Test get_events with type filter."""
        from forge_harness.pipeline_callbacks import (
            AggregatingCallback,
            EventType,
            PipelineEvent,
        )

        callback = AggregatingCallback()

        # Add different event types
        await callback.on_event(
            PipelineEvent(
                event_type=EventType.STEP_STARTED,
                pipeline_name="test",
                step_name="step1",
            )
        )
        await callback.on_event(
            PipelineEvent(
                event_type=EventType.STEP_COMPLETED,
                pipeline_name="test",
                step_name="step1",
            )
        )
        await callback.on_event(
            PipelineEvent(
                event_type=EventType.STEP_STARTED,
                pipeline_name="test",
                step_name="step2",
            )
        )

        # Filter by event type
        started_events = callback.get_events(EventType.STEP_STARTED)
        assert len(started_events) == 2
        assert all(e.event_type == EventType.STEP_STARTED for e in started_events)

        completed_events = callback.get_events(EventType.STEP_COMPLETED)
        assert len(completed_events) == 1

    @pytest.mark.asyncio
    async def test_aggregating_callback_clear(self):
        """Test clear method."""
        from forge_harness.pipeline_callbacks import (
            AggregatingCallback,
            EventType,
            PipelineEvent,
        )

        callback = AggregatingCallback()

        event = PipelineEvent(
            event_type=EventType.STEP_STARTED,
            pipeline_name="test",
        )
        await callback.on_event(event)

        assert len(callback.get_events()) == 1

        callback.clear()

        assert len(callback.get_events()) == 0


class TestCallbackManager:
    """Tests for CallbackManager."""

    def test_callback_manager(self):
        """Test callback manager registration."""
        from forge_harness.pipeline_callbacks import (
            AggregatingCallback,
            CallbackManager,
        )

        manager = CallbackManager()
        callback = AggregatingCallback()

        assert manager.callback_count == 0

        manager.register(callback)
        assert manager.callback_count == 1

        # Duplicate registration should not add twice
        manager.register(callback)
        assert manager.callback_count == 1

    @pytest.mark.asyncio
    async def test_callback_manager_emit(self):
        """Test callback manager emits to all callbacks."""
        from forge_harness.pipeline_callbacks import (
            AggregatingCallback,
            CallbackManager,
            EventType,
            PipelineEvent,
        )

        manager = CallbackManager()
        callback1 = AggregatingCallback()
        callback2 = AggregatingCallback()

        manager.register(callback1)
        manager.register(callback2)

        event = PipelineEvent(
            event_type=EventType.STEP_STARTED,
            pipeline_name="test",
        )
        await manager.emit(event)

        # Both callbacks should have received the event
        assert len(callback1.get_events()) == 1
        assert len(callback2.get_events()) == 1

    def test_callback_manager_unregister(self):
        """Test callback manager unregistration."""
        from forge_harness.pipeline_callbacks import (
            AggregatingCallback,
            CallbackManager,
        )

        manager = CallbackManager()
        callback = AggregatingCallback()

        manager.register(callback)
        assert manager.callback_count == 1

        manager.unregister(callback)
        assert manager.callback_count == 0

        # Unregistering again should not raise
        manager.unregister(callback)
        assert manager.callback_count == 0

    @pytest.mark.asyncio
    async def test_callback_manager_error_handling(self):
        """Test callback manager handles callback errors."""
        from forge_harness.pipeline_callbacks import (
            AggregatingCallback,
            CallbackManager,
            EventType,
            PipelineEvent,
        )

        # Create a callback that raises an error
        class FailingCallback:
            async def on_event(self, event):
                raise ValueError("Test error")

        manager = CallbackManager()
        failing_callback = FailingCallback()
        good_callback = AggregatingCallback()

        # Register failing callback first, then good callback
        manager.register(failing_callback)
        manager.register(good_callback)

        event = PipelineEvent(
            event_type=EventType.STEP_STARTED,
            pipeline_name="test",
        )

        # Should not raise, and good callback should still receive event
        await manager.emit(event)

        assert len(good_callback.get_events()) == 1


class TestOrchestrationCallbackIntegration:
    """Tests for callback integration with OrchestrationHarness."""

    def test_orchestration_callbacks(self, tmp_path):
        """Test registering callbacks with OrchestrationHarness."""
        from forge_harness.orchestration_harness import OrchestrationHarness
        from forge_harness.pipeline_callbacks import AggregatingCallback

        orchestrator = OrchestrationHarness(
            harnesses={},
            checkpoint_dir=tmp_path,
            enable_checkpoints=False,
        )

        callback = AggregatingCallback()

        # Should have 0 callbacks by default
        assert orchestrator._callback_manager.callback_count == 0

        # Register callback
        orchestrator.register_callback(callback)
        assert orchestrator._callback_manager.callback_count == 1

        # Unregister callback
        orchestrator.unregister_callback(callback)
        assert orchestrator._callback_manager.callback_count == 0

    @pytest.mark.asyncio
    async def test_orchestration_callback_events(self, tmp_path):
        """Test callbacks receive events during execution."""
        from forge_harness.orchestration_harness import (
            OrchestrationHarness,
            Pipeline,
            PipelineStep,
        )
        from forge_harness.pipeline_callbacks import (
            AggregatingCallback,
            EventType,
        )

        # Create a mock harness with an async method
        class MockHarness:
            async def test_method(self, **kwargs):
                return {"result": "success"}

        orchestrator = OrchestrationHarness(
            harnesses={"mock": MockHarness()},
            checkpoint_dir=tmp_path,
            enable_checkpoints=False,
        )

        # Register aggregating callback
        callback = AggregatingCallback()
        orchestrator.register_callback(callback)

        # Define a simple pipeline
        pipeline = Pipeline(
            name="test_pipeline",
            steps=[
                PipelineStep(
                    name="step1",
                    harness="mock",
                    method="test_method",
                    inputs={},
                    outputs=["result"],
                ),
            ],
        )

        # Execute pipeline
        result = await orchestrator.execute(pipeline)

        # Verify events were emitted
        events = callback.get_events()
        assert (
            len(events) >= 3
        )  # At least: pipeline_started, step_started, step_completed, pipeline_completed

        # Check specific events
        event_types = [e.event_type for e in events]
        assert EventType.PIPELINE_STARTED in event_types
        assert EventType.STEP_STARTED in event_types
        assert EventType.STEP_COMPLETED in event_types
        assert EventType.PIPELINE_COMPLETED in event_types

        # Verify pipeline name is set correctly
        for event in events:
            assert event.pipeline_name == "test_pipeline"

        # Verify step events have step_name
        step_events = [e for e in events if e.step_name is not None]
        assert len(step_events) >= 2  # step_started, step_completed
        assert all(e.step_name == "step1" for e in step_events)
