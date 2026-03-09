"""
Memory Flush Module - Pre-Compaction Context Preservation
==========================================================

Based on MoltBot research: Before context window compaction (when approaching
token limits), trigger a "memory save" prompt to preserve key decisions,
patterns, and technical context for future sessions.

This prevents nuance loss during compression and enables better session handoffs.

Usage:
    from forge_harness.memory_flush import MemoryFlushManager, ContextMonitor

    # Monitor context usage
    monitor = ContextMonitor(threshold=0.7)
    if monitor.should_flush(current_tokens=80000, max_tokens=100000):
        # Trigger memory flush
        manager = MemoryFlushManager(forge_root=Path("."))
        summary = await manager.trigger_flush(
            context="Current task context...",
            provider=llm_provider,
        )
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class ContextMetrics:
    """Metrics for context window usage."""

    current_tokens: int
    max_tokens: int
    usage_percent: float
    threshold: float
    should_flush: bool
    time_checked: datetime

    @classmethod
    def from_usage(
        cls,
        current_tokens: int,
        max_tokens: int,
        threshold: float = 0.7,
    ) -> "ContextMetrics":
        """Create metrics from token usage."""
        usage_percent = current_tokens / max_tokens if max_tokens > 0 else 0.0
        should_flush = usage_percent >= threshold

        return cls(
            current_tokens=current_tokens,
            max_tokens=max_tokens,
            usage_percent=usage_percent,
            threshold=threshold,
            should_flush=should_flush,
            time_checked=datetime.now(UTC),
        )


class ContextMonitor:
    """
    Monitors context window usage and determines when to trigger memory flush.

    Based on MoltBot research: Flush at 70% context usage before compaction.
    """

    def __init__(self, threshold: float = 0.7):
        """
        Initialize context monitor.

        Args:
            threshold: Context usage percentage to trigger flush (0.0-1.0)
        """
        if not 0.0 <= threshold <= 1.0:
            raise ValueError("Threshold must be between 0.0 and 1.0")

        self.threshold = threshold
        self._last_flush: datetime | None = None
        self._flush_count = 0

    def should_flush(
        self,
        current_tokens: int,
        max_tokens: int,
        min_interval_minutes: int = 30,
    ) -> bool:
        """
        Check if memory flush should be triggered.

        Args:
            current_tokens: Current token count in context
            max_tokens: Maximum token capacity
            min_interval_minutes: Minimum minutes between flushes

        Returns:
            True if flush should be triggered
        """
        metrics = ContextMetrics.from_usage(current_tokens, max_tokens, self.threshold)

        # Check usage threshold
        if not metrics.should_flush:
            return False

        # Check minimum interval if we've flushed before
        if self._last_flush:
            elapsed = (datetime.now(UTC) - self._last_flush).total_seconds()
            if elapsed < min_interval_minutes * 60:
                logger.debug(
                    f"Flush requested but minimum interval not met "
                    f"(elapsed: {elapsed / 60:.1f}m, required: {min_interval_minutes}m)"
                )
                return False

        return True

    def record_flush(self) -> None:
        """Record that a flush was performed."""
        self._last_flush = datetime.now(UTC)
        self._flush_count += 1
        logger.info(f"Memory flush #{self._flush_count} recorded at {self._last_flush}")


class MemoryFlushManager:
    """
    Manages pre-compaction memory flushing to preserve context.

    Creates session notes in `.forge/learning/session_notes/YYYY-MM-DD.md`
    with key decisions, patterns, and technical context.
    """

    def __init__(self, forge_root: Path):
        """
        Initialize memory flush manager.

        Args:
            forge_root: Path to FORGE repository root
        """
        self.forge_root = Path(forge_root)
        self.session_notes_dir = self.forge_root / ".forge/learning" / "session_notes"
        self.session_notes_dir.mkdir(parents=True, exist_ok=True)

    def _get_session_note_path(self, date: datetime | None = None) -> Path:
        """
        Get path for session note file.

        Args:
            date: Date for session note (defaults to today)

        Returns:
            Path to session note file
        """
        if date is None:
            date = datetime.now(UTC)

        filename = date.strftime("%Y-%m-%d.md")
        return self.session_notes_dir / filename

    def _build_memory_prompt(self, context: str) -> str:
        """
        Build prompt for LLM to generate memory summary.

        Args:
            context: Current task/feature context

        Returns:
            Prompt string for memory extraction
        """
        return f"""# Memory Save Request

Your context window is approaching capacity (>70%). Before compaction, please summarize the key information that future sessions will need to continue this work effectively.

## Current Context
{context}

## Memory Save Format

Please provide a structured summary in the following format:

### Key Decisions Made
- List each major decision with its rationale
- Include architectural choices
- Note any trade-offs considered

### Patterns Discovered
- What worked well (and why)
- What failed (and why)
- Lessons learned for similar features

### Technical Context
- Critical file locations and their purposes
- Important code patterns or conventions
- Dependencies or integrations to be aware of
- Any workarounds or gotchas

### Next Steps
- Immediate next tasks
- Open questions that need resolution
- Blockers or dependencies

Please be concise but complete. Focus on information that cannot be easily recovered from code or git history."""

    async def trigger_flush(
        self,
        context: str,
        provider: Any,
        domain: str | None = None,
        project: str | None = None,
        feature_id: str | None = None,
    ) -> str:
        """
        Trigger memory flush and save to session notes.

        Args:
            context: Current task/feature context
            provider: LLM provider instance with query method
            domain: Optional domain name
            project: Optional project name
            feature_id: Optional feature ID being worked on

        Returns:
            Generated memory summary
        """
        logger.info("Triggering pre-compaction memory flush...")

        # Build memory extraction prompt
        memory_prompt = self._build_memory_prompt(context)

        try:
            # Query LLM for memory summary
            memory_summary = await self._query_llm(memory_prompt, provider)

            # Save to session notes
            await self._save_session_note(
                memory_summary=memory_summary,
                context=context,
                domain=domain,
                project=project,
                feature_id=feature_id,
            )

            logger.info(f"Memory flush completed. Saved to {self._get_session_note_path()}")
            return memory_summary

        except Exception as e:
            logger.error(f"Memory flush failed: {e}", exc_info=True)
            # Still save a minimal note even if LLM query fails
            await self._save_fallback_note(context, str(e))
            return f"Memory flush failed: {e}"

    async def _query_llm(self, prompt: str, provider: Any) -> str:
        """
        Query LLM provider for memory summary.

        Args:
            prompt: Memory extraction prompt
            provider: LLM provider instance

        Returns:
            Memory summary from LLM
        """
        # Try different provider interfaces
        if hasattr(provider, "query") and callable(provider.query):
            response = await provider.query(prompt)
        elif hasattr(provider, "run_coding_session") and callable(provider.run_coding_session):
            # For ClaudeCodeProvider, collect streaming response
            response = ""
            async for message in provider.run_coding_session(prompt):
                if hasattr(message, "content"):
                    response += str(message.content) + "\n"
        else:
            raise ValueError(
                f"Provider {type(provider).__name__} does not support query or run_coding_session"
            )

        return response.strip()

    async def _save_session_note(
        self,
        memory_summary: str,
        context: str,
        domain: str | None = None,
        project: str | None = None,
        feature_id: str | None = None,
    ) -> None:
        """
        Save memory summary to session notes file.

        Args:
            memory_summary: Generated memory summary
            context: Original context
            domain: Optional domain name
            project: Optional project name
            feature_id: Optional feature ID
        """
        note_path = self._get_session_note_path()
        timestamp = datetime.now(UTC).strftime("%H:%M:%S UTC")

        # Build metadata header
        metadata = [f"**Time:** {timestamp}"]
        if domain:
            metadata.append(f"**Domain:** {domain}")
        if project:
            metadata.append(f"**Project:** {project}")
        if feature_id:
            metadata.append(f"**Feature:** {feature_id}")

        # Build note entry
        note_entry = f"""
---

## Memory Flush - {timestamp}

{chr(10).join(metadata)}

### Original Context
```
{context[:500]}{"..." if len(context) > 500 else ""}
```

### Memory Summary

{memory_summary}

"""

        # Append to existing file or create new
        if note_path.exists():
            content = note_path.read_text()
            content += note_entry
        else:
            # Create new file with header
            date_str = datetime.now(UTC).strftime("%Y-%m-%d")
            content = f"""# Session Notes - {date_str}

Memory flushes and context preservation for autonomous agent sessions.

{note_entry}
"""

        note_path.write_text(content)
        logger.debug(f"Session note saved to {note_path}")

    async def _save_fallback_note(self, context: str, error: str) -> None:
        """
        Save fallback note when LLM query fails.

        Args:
            context: Original context
            error: Error message
        """
        note_path = self._get_session_note_path()
        timestamp = datetime.now(UTC).strftime("%H:%M:%S UTC")

        note_entry = f"""
---

## Memory Flush FAILED - {timestamp}

**Error:** {error}

### Context Snapshot
```
{context[:1000]}{"..." if len(context) > 1000 else ""}
```

"""

        if note_path.exists():
            content = note_path.read_text()
            content += note_entry
        else:
            date_str = datetime.now(UTC).strftime("%Y-%m-%d")
            content = f"""# Session Notes - {date_str}

{note_entry}
"""

        note_path.write_text(content)
        logger.warning(f"Fallback session note saved to {note_path}")

    def get_recent_notes(self, days: int = 7) -> list[tuple[datetime, str]]:
        """
        Retrieve recent session notes.

        Args:
            days: Number of days of notes to retrieve

        Returns:
            List of (date, content) tuples
        """
        notes = []
        for i in range(days):
            date = datetime.now(UTC) - __import__("datetime").timedelta(days=i)
            note_path = self._get_session_note_path(date)

            if note_path.exists():
                content = note_path.read_text()
                notes.append((date, content))

        return notes


async def integrate_memory_flush_into_loop(
    loop_instance: Any,
    provider: Any,
    context_threshold: float = 0.7,
) -> None:
    """
    Helper function to integrate memory flush into an existing ralph loop.

    Args:
        loop_instance: RalphLoopHarness instance
        provider: LLM provider instance
        context_threshold: Threshold for triggering flush
    """
    monitor = ContextMonitor(threshold=context_threshold)
    manager = MemoryFlushManager(forge_root=loop_instance.store.features_path.parent)

    # Add monitor and manager to loop instance
    loop_instance._memory_monitor = monitor
    loop_instance._memory_manager = manager
    loop_instance._memory_provider = provider

    logger.info(f"Memory flush integration complete (threshold: {context_threshold})")
