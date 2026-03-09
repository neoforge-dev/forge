"""OpenClaw Bridge - Telegram/Slack → V3 SQLite.

Connects human messaging interfaces (Telegram bot, Slack slash commands)
to the V3 task management system.

Usage:
    bridge = OpenClawBridge()
    
    # Telegram webhook handler
    @app.post("/webhook/telegram")
    def telegram_webhook(update: TelegramUpdate):
        result = bridge.handle_telegram(update.dict())
        return result
    
    # Slack webhook handler
    @app.post("/webhook/slack")
    def slack_webhook(command: SlackCommand):
        result = bridge.handle_slack(command.dict())
        return result
"""

import logging
from typing import Any

from .database import get_task_db

logger = logging.getLogger(__name__)


class OpenClawBridge:
    """Bridge between messaging platforms and V3 task system."""

    def __init__(self):
        self.db = get_task_db()

    def handle_telegram(self, update: dict[str, Any]) -> dict[str, Any]:
        """Handle Telegram bot webhook.
        
        Expected message format:
            /task <title> - <description>
            /task Fix auth bug - Users can't login with Google
            /bug Critical error in payment
            /feature Add dark mode
        """
        try:
            message = update.get("message", {})
            text = message.get("text", "")
            chat_id = message.get("chat", {}).get("id")
            user = message.get("from", {})
            username = user.get("username", user.get("first_name", "unknown"))

            # Parse command
            if not text.startswith("/"):
                return {
                    "ok": False,
                    "error": "Not a command. Use /task, /bug, or /feature"
                }

            # Extract command and content
            parts = text.split(maxsplit=1)
            command = parts[0].lower()
            content = parts[1] if len(parts) > 1 else ""

            # Map commands to priorities
            priority_map = {
                "/task": "medium",
                "/bug": "high",
                "/feature": "medium",
                "/critical": "critical",
                "/urgent": "high"
            }

            priority = priority_map.get(command, "medium")

            # Empty command check
            if not content:
                return {
                    "ok": False,
                    "error": "Please provide a task title: /task <title> - <description>"
                }

            # Parse title and description
            if " - " in content:
                title, description = content.split(" - ", maxsplit=1)
            else:
                title = content
                description = f"Created via Telegram by {username}"

            title = title.strip()[:100]
            description = description.strip()[:500]

            if not title:
                return {
                    "ok": False,
                    "error": "Please provide a task title: /task <title> - <description>"
                }

            # Create V3 task
            source_file = f"telegram://{chat_id}/{message.get('message_id')}"
            task_id = self.db.create_task(
                title=title,
                description=description,
                priority=priority,
                source_file=source_file,
                result_path=None,
                metadata={
                    "source": "telegram",
                    "chat_id": chat_id,
                    "username": username,
                    "telegram_message_id": message.get("message_id"),
                    "command": command,
                    "created_via": "openclaw_bridge"
                }
            )

            logger.info(f"Created task {task_id} from Telegram user {username}")

            return {
                "ok": True,
                "task_id": task_id,
                "title": title,
                "priority": priority,
                "message": f"✅ Task created: {task_id}\nTitle: {title}\nPriority: {priority}"
            }

        except Exception as e:
            logger.error(f"Failed to handle Telegram message: {e}")
            return {
                "ok": False,
                "error": f"Internal error: {str(e)}"
            }

    def handle_slack(self, command: dict[str, Any]) -> dict[str, Any]:
        """Handle Slack slash command webhook.
        
        Expected command format:
            /forge-task <title> - <description>
            /forge-task Fix auth bug - Users can't login
        """
        try:
            text = command.get("text", "")
            user_id = command.get("user_id", "unknown")
            user_name = command.get("user_name", "unknown")
            channel_id = command.get("channel_id", "")
            command_name = command.get("command", "/forge-task")

            # Parse title and description
            if " - " in text:
                title, description = text.split(" - ", maxsplit=1)
            else:
                title = text or f"Task from {user_name}"
                description = f"Created via Slack by {user_name}"

            title = title.strip()[:100]
            description = description.strip()[:500]

            if not title:
                return {
                    "ok": False,
                    "error": "Please provide a task title: /forge-task <title> - <description>"
                }

            # Determine priority from command or content
            priority = "medium"
            if any(word in title.lower() for word in ["critical", "urgent", "crash", "down"]):
                priority = "critical"
            elif any(word in title.lower() for word in ["bug", "error", "fix"]):
                priority = "high"

            # Create V3 task
            task_id = self.db.create_task(
                title=title,
                description=description,
                priority=priority,
                source_file=f"slack://{channel_id}/{user_id}",
                result_path=None,
                metadata={
                    "source": "slack",
                    "channel_id": channel_id,
                    "user_id": user_id,
                    "user_name": user_name,
                    "command": command_name,
                    "created_via": "openclaw_bridge"
                }
            )

            logger.info(f"Created task {task_id} from Slack user {user_name}")

            # Return Slack-compatible response
            return {
                "ok": True,
                "response_type": "ephemeral",
                "text": f"✅ Task created: `{task_id}`\n*Title:* {title}\n*Priority:* {priority}",
                "attachments": [
                    {
                        "color": "good",
                        "fields": [
                            {"title": "Task ID", "value": task_id, "short": True},
                            {"title": "Priority", "value": priority, "short": True},
                            {"title": "Title", "value": title, "short": False}
                        ]
                    }
                ]
            }

        except Exception as e:
            logger.error(f"Failed to handle Slack command: {e}")
            return {
                "ok": False,
                "error": f"Internal error: {str(e)}"
            }

    def get_task_status_for_user(self, task_id: str, platform: str = "telegram") -> str:
        """Get human-readable task status."""
        status = self.db.get_task_status(task_id)

        if not status:
            return f"❌ Task {task_id} not found"

        emoji_map = {
            "pending": "⏳",
            "assigned": "👤",
            "in_progress": "🔄",
            "completed": "✅",
            "failed": "❌",
            "blocked": "🚫"
        }

        emoji = emoji_map.get(status["status"], "❓")
        agent = status.get("assigned_agent", "Unassigned")

        return (
            f"{emoji} Task: {task_id}\n"
            f"Status: {status['status']}\n"
            f"Agent: {agent}\n"
            f"Title: {status['title'][:50]}"
        )


# FastAPI routes for webhooks
def create_webhook_routes(app):
    """Add webhook routes to FastAPI app."""
    bridge = OpenClawBridge()

    @app.post("/webhook/telegram")
    async def telegram_webhook(update: dict):
        """Receive Telegram bot updates."""
        result = bridge.handle_telegram(update)

        # If we need to reply via Telegram API
        if result.get("ok") and "chat_id" in str(update):
            # In production, call Telegram Bot API here
            pass

        return result

    @app.post("/webhook/slack")
    async def slack_webhook(command: dict):
        """Receive Slack slash commands."""
        return bridge.handle_slack(command)

    @app.get("/webhook/telegram")
    async def telegram_verify():
        """Verify Telegram webhook (GET request for setup)."""
        return {"ok": True, "message": "Webhook ready"}


# Singleton
_bridge_instance: OpenClawBridge | None = None

def get_openclaw_bridge() -> OpenClawBridge:
    """Get singleton OpenClaw bridge instance."""
    global _bridge_instance
    if _bridge_instance is None:
        _bridge_instance = OpenClawBridge()
    return _bridge_instance
