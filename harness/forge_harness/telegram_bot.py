"""
Telegram Bot Handler for FORGE Command Center
==============================================

Phase 2: Live Fleet Commands
- Bot initialization with token
- Webhook handler for incoming messages
- Full command set: /start, /status, /help, /dispatch, /approve, /tasks
- Send notification method
- Intent-based free-text routing

Usage:
    from forge_harness.telegram_bot import TelegramBotHandler

    handler = TelegramBotHandler(
        bot_token="your_bot_token",
        allowed_users={"123456789"},
    )

    # Send notification
    await handler.send_message(
        chat_id="123456789",
        text="Task completed successfully!"
    )

    # Handle webhook update
    result = await handler.handle_update(update_dict)

Environment:
    TELEGRAM_BOT_TOKEN - Bot token from @BotFather
    TELEGRAM_ALLOWED_USERS - Comma-separated list of allowed chat IDs
    TELEGRAM_WEBHOOK_SECRET - Secret token for webhook validation
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx

from .logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class TelegramUser:
    """Telegram user configuration."""

    user_id: str
    username: str | None = None
    first_name: str | None = None
    role: str = "readonly"  # admin, readonly
    notifications_enabled: bool = True


@dataclass
class TelegramCommand:
    """Parsed Telegram command."""

    name: str
    args: list[str]
    raw_text: str
    chat_id: str
    user_id: str
    message_id: int | None = None
    username: str | None = None
    first_name: str | None = None


@dataclass
class TelegramMessage:
    """Telegram message for sending."""

    text: str
    chat_id: str
    parse_mode: str = "HTML"
    disable_notification: bool = False
    reply_markup: dict | None = None


class TelegramBotHandler:
    """
    Handler for Telegram Bot API interactions.

    Supports:
    - Sending notifications to authorized users
    - Receiving and parsing commands
    - Webhook signature verification
    - Tier-aware message formatting

    Usage:
        handler = TelegramBotHandler.from_env()
        
        # Send simple message
        await handler.send_message("123456789", "Hello!")
        
        # Handle incoming webhook
        result = await handler.handle_update(update_data)
    """

    TELEGRAM_API_BASE = "https://api.telegram.org/bot"

    def __init__(
        self,
        bot_token: str | None = None,
        allowed_users: set[str] | None = None,
        webhook_secret: str | None = None,
    ):
        """
        Initialize Telegram bot handler.

        Args:
            bot_token: Telegram bot token from @BotFather
            allowed_users: Set of allowed chat/user IDs
            webhook_secret: Secret token for webhook validation
        """
        self.bot_token = bot_token
        self.allowed_users = allowed_users or set()
        self.webhook_secret = webhook_secret

        # HTTP client for API calls
        self._http_client: httpx.AsyncClient | None = None

        # Track authorized users who have started the bot
        self._authorized_chats: dict[str, TelegramUser] = {}

    @classmethod
    def from_env(cls) -> TelegramBotHandler:
        """Create handler from environment variables."""
        bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")

        allowed_users_str = os.environ.get("TELEGRAM_ALLOWED_USERS", "")
        allowed_users = set(
            user_id.strip()
            for user_id in allowed_users_str.split(",")
            if user_id.strip()
        )

        webhook_secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET")

        return cls(
            bot_token=bot_token,
            allowed_users=allowed_users,
            webhook_secret=webhook_secret,
        )

    def is_configured(self) -> bool:
        """Check if bot is properly configured."""
        return bool(self.bot_token and self.allowed_users)

    def is_authorized(self, chat_id: str) -> bool:
        """Check if chat/user ID is authorized."""
        return chat_id in self.allowed_users

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=30.0)
        return self._http_client

    async def close(self) -> None:
        """Close HTTP client."""
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

    def verify_webhook(self, headers: dict[str, str]) -> bool:
        """
        Verify Telegram webhook request.

        Args:
            headers: Request headers

        Returns:
            True if webhook is verified or no secret configured
        """
        if not self.webhook_secret:
            return True

        secret_header = headers.get("X-Telegram-Bot-Api-Secret-Token")
        return secret_header == self.webhook_secret

    async def send_message(
        self,
        chat_id: str,
        text: str,
        parse_mode: str = "HTML",
        disable_notification: bool = False,
        reply_markup: dict | None = None,
    ) -> dict[str, Any]:
        """
        Send message via Telegram Bot API.

        Args:
            chat_id: Target chat ID
            text: Message text (HTML formatted)
            parse_mode: Parse mode (HTML, Markdown, MarkdownV2)
            disable_notification: Send silently
            reply_markup: Optional inline keyboard markup

        Returns:
            API response data

        Raises:
            ValueError: If bot token not configured
            httpx.HTTPError: If API request fails
        """
        if not self.bot_token:
            raise ValueError("Telegram bot token not configured")

        if not self.is_authorized(chat_id):
            logger.warning(f"Unauthorized chat ID: {chat_id}")
            raise ValueError(f"Chat ID {chat_id} not authorized")

        url = f"{self.TELEGRAM_API_BASE}{self.bot_token}/sendMessage"

        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_notification": disable_notification,
        }

        if reply_markup:
            payload["reply_markup"] = reply_markup

        client = await self._get_client()
        response = await client.post(url, json=payload)
        response.raise_for_status()

        data = response.json()
        if not data.get("ok"):
            logger.error(f"Telegram API error: {data}")
            raise ValueError(f"Telegram API error: {data.get('description')}")

        logger.debug(f"Message sent to {chat_id}")
        return data["result"]

    async def send_notification(
        self,
        chat_id: str,
        title: str,
        message: str,
        tier: str = "phone",
        metadata: dict[str, Any] | None = None,
        action_url: str | None = None,
    ) -> dict[str, Any]:
        """
        Send tier-aware notification.

        Args:
            chat_id: Target chat ID
            title: Notification title
            message: Message body
            tier: Notification tier (watch, phone, desktop)
            metadata: Additional metadata to display
            action_url: Optional action URL

        Returns:
            API response data
        """
        text = self._format_notification_text(
            title=title,
            message=message,
            tier=tier,
            metadata=metadata,
            action_url=action_url,
        )

        reply_markup = None
        if action_url and tier in ("phone", "desktop"):
            reply_markup = {
                "inline_keyboard": [
                    [{"text": "Open Dashboard", "url": action_url}]
                ]
            }

        return await self.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
        )

    async def send_approval_request(
        self,
        chat_id: str,
        request_id: str,
        title: str,
        description: str,
        tier: str = "phone",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Send approval request with approve/reject buttons.

        Args:
            chat_id: Target chat ID
            request_id: Unique approval request ID
            title: Approval title
            description: Approval description
            tier: Notification tier
            metadata: Additional context

        Returns:
            API response data
        """
        text = self._format_notification_text(
            title=f"Approval Required: {title}",
            message=description,
            tier=tier,
            metadata=metadata,
        )

        # Build inline keyboard based on tier
        if tier == "watch":
            # Minimal - just approve/reject
            reply_markup = {
                "inline_keyboard": [
                    [
                        {"text": "✅ Approve", "callback_data": f"approve:{request_id}"},
                        {"text": "❌ Reject", "callback_data": f"reject:{request_id}"},
                    ]
                ]
            }
        else:
            # Full - approve, reject, details
            reply_markup = {
                "inline_keyboard": [
                    [
                        {"text": "✅ Approve", "callback_data": f"approve:{request_id}"},
                        {"text": "❌ Reject", "callback_data": f"reject:{request_id}"},
                    ],
                    [
                        {"text": "📄 View Details", "callback_data": f"details:{request_id}"},
                    ],
                ]
            }

        return await self.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
        )

    def _format_notification_text(
        self,
        title: str,
        message: str,
        tier: str = "phone",
        metadata: dict[str, Any] | None = None,
        action_url: str | None = None,
    ) -> str:
        """
        Format notification text based on tier.

        Tiers:
        - WATCH: Minimal, Apple Watch friendly (< 100 chars)
        - PHONE: Summary format for mobile
        - DESKTOP: Full details
        """
        metadata = metadata or {}

        if tier == "watch":
            # Ultra-compact for watch
            emoji = self._get_emoji_for_type(metadata.get("type", "info"))
            short_title = title[:50] + "..." if len(title) > 50 else title
            return f"{emoji} <b>{short_title}</b>"

        elif tier == "phone":
            # Mobile-friendly format
            lines = []
            emoji = self._get_emoji_for_type(metadata.get("type", "info"))
            lines.append(f"{emoji} <b>{title}</b>")
            lines.append("")

            # Brief context
            context_parts = []
            if metadata.get("domain"):
                context_parts.append(f"📁 {metadata['domain']}")
            if metadata.get("project"):
                context_parts.append(f"🗂️ {metadata['project']}")
            if context_parts:
                lines.append(" | ".join(context_parts))
                lines.append("")

            # Truncated message
            short_msg = message[:200] + "..." if len(message) > 200 else message
            lines.append(short_msg)

            return "\n".join(lines)

        else:  # desktop
            # Full format
            lines = []
            emoji = self._get_emoji_for_type(metadata.get("type", "info"))
            lines.append(f"{emoji} <b>{title}</b>")
            lines.append("")

            # Context metadata
            if metadata.get("domain"):
                lines.append(f"📁 <b>Domain:</b> {metadata['domain']}")
            if metadata.get("project"):
                lines.append(f"🗂️ <b>Project:</b> {metadata['project']}")
            if metadata.get("feature_id"):
                lines.append(f"🔖 <b>Feature:</b> {metadata['feature_id']}")
            if metadata.get("risk_score") is not None:
                risk = metadata["risk_score"]
                risk_emoji = "🟢" if risk < 0.3 else "🟡" if risk < 0.7 else "🔴"
                lines.append(f"{risk_emoji} <b>Risk:</b> {risk:.0%}")

            if len(lines) > 2:
                lines.append("")

            lines.append(message)

            if action_url:
                lines.append("")
                lines.append(f"<a href='{action_url}'>Open Dashboard</a>")

            return "\n".join(lines)

    def _get_emoji_for_type(self, msg_type: str) -> str:
        """Get emoji for message type."""
        return {
            "approval": "🔘",
            "task_complete": "✅",
            "error": "❌",
            "warning": "⚠️",
            "info": "ℹ️",
            "summary": "📊",
        }.get(msg_type, "ℹ️")

    async def handle_update(self, update: dict[str, Any]) -> dict[str, Any]:
        """
        Process incoming Telegram update.

        Args:
            update: Telegram update object

        Returns:
            Result of handling
        """
        logger.debug(f"Received update: {update}")

        # Handle callback queries (button clicks)
        if "callback_query" in update:
            return await self._handle_callback(update["callback_query"])

        # Handle messages (commands and text)
        if "message" in update:
            message = update["message"]
            chat_id = str(message["chat"]["id"])
            user_id = str(message["from"]["id"])

            # Check authorization
            if not self.is_authorized(chat_id):
                logger.warning(f"Unauthorized message from chat {chat_id}")
                await self.send_message(
                    chat_id=chat_id,
                    text="❌ You are not authorized to use this bot.",
                )
                return {"ok": False, "error": "unauthorized"}

            # Store user info
            self._authorized_chats[chat_id] = TelegramUser(
                user_id=user_id,
                username=message["from"].get("username"),
                first_name=message["from"].get("first_name"),
            )

            # Parse and handle command
            if "text" in message:
                text = message["text"]
                if text.startswith("/"):
                    return await self._handle_command(message)
                else:
                    return await self._handle_text_message(message)

        return {"ok": True, "handled": False}

    async def _handle_command(self, message: dict[str, Any]) -> dict[str, Any]:
        """Handle bot command."""
        text = message["text"]
        chat_id = str(message["chat"]["id"])

        # Parse command
        parts = text.split()
        command = parts[0].split("@")[0].lower()  # Remove bot username if present
        args = parts[1:]

        logger.info(f"Command received: {command} from chat {chat_id}")

        # Route to handler
        if command == "/start":
            return await self._cmd_start(chat_id, message)
        elif command == "/help":
            return await self._cmd_help(chat_id)
        elif command == "/status":
            return await self._cmd_status(chat_id, args)
        elif command == "/dispatch":
            return await self._cmd_dispatch(chat_id, args)
        elif command == "/approve":
            return await self._cmd_approve(chat_id, args)
        elif command == "/tasks":
            return await self._cmd_tasks(chat_id, args)
        elif command == "/spawn":
            return await self._cmd_spawn(chat_id, args)
        elif command == "/stop":
            return await self._cmd_stop(chat_id, args)
        elif command == "/nudge":
            return await self._cmd_nudge(chat_id, args)
        elif command == "/rotate":
            return await self._cmd_rotate(chat_id, args)
        else:
            await self.send_message(
                chat_id=chat_id,
                text=f"❓ Unknown command: {command}\n\nUse /help to see available commands.",
            )
            return {"ok": True, "command": command, "handled": False}

    async def _handle_text_message(self, message: dict[str, Any]) -> dict[str, Any]:
        """Handle plain text message — route through intent parser."""
        chat_id = str(message["chat"]["id"])
        text = message["text"]

        try:
            from forge_harness.openclaw.openclaw_gateway import parse_intent

            intent = parse_intent(text)

            if intent.kind == "status":
                return await self._cmd_status(chat_id, [])
            elif intent.kind == "approval":
                return await self._cmd_approve(chat_id, [])
            elif intent.kind == "direct" and intent.agent:
                return await self._cmd_dispatch(chat_id, [intent.agent, intent.text])
            else:
                # Default: acknowledge as task creation
                await self.send_message(
                    chat_id=chat_id,
                    text=f"Understood. Creating task:\n<i>{text[:100]}</i>\n\nUse /dispatch <agent> to assign it.",
                )
                return {"ok": True, "handled": True, "type": "text", "intent": intent.kind}
        except Exception as e:
            logger.error(f"Text message handling failed: {e}")
            await self.send_message(
                chat_id=chat_id,
                text="I understand commands. Use /help to see what I can do!",
            )
            return {"ok": True, "handled": True, "type": "text"}

    async def _handle_callback(self, callback_query: dict[str, Any]) -> dict[str, Any]:
        """Handle inline keyboard callback — process approvals via CC backend."""
        data = callback_query.get("data", "")
        chat_id = str(callback_query["message"]["chat"]["id"])
        user_id = str(callback_query.get("from", {}).get("id", chat_id))

        # Check authorization
        if not self.is_authorized(chat_id):
            logger.warning(f"Unauthorized callback from chat {chat_id}")
            return {"ok": False, "error": "unauthorized"}

        logger.info(f"Callback received: {data} from {chat_id}")

        if ":" not in data:
            return {"ok": True, "handled": False}

        action, request_id = data.split(":", 1)

        try:
            from forge_harness.webhook_server.handlers.approval_handler import get_approval_handler

            handler = get_approval_handler()

            if action == "approve":
                await handler.approve_request(
                    request_id=request_id,
                    approver=user_id,
                )
                await self.send_message(
                    chat_id=chat_id,
                    text=f"✅ <b>Approved:</b> <code>{request_id}</code>",
                )
                return {"ok": True, "action": "approve", "request_id": request_id, "result": "processed"}

            elif action == "reject":
                await handler.reject_request(
                    request_id=request_id,
                    rejector=user_id,
                )
                await self.send_message(
                    chat_id=chat_id,
                    text=f"❌ <b>Rejected:</b> <code>{request_id}</code>",
                )
                return {"ok": True, "action": "reject", "request_id": request_id, "result": "processed"}

            elif action == "details":
                details = await handler.get_request(request_id)
                if details:
                    title = details.get("title", "Untitled")
                    desc = details.get("description", "No description")[:300]
                    status = details.get("status", "unknown")
                    priority = details.get("priority", "unknown")
                    created = details.get("created_at", "?")

                    text = (
                        f"📄 <b>Approval Details</b>\n\n"
                        f"<b>ID:</b> <code>{request_id}</code>\n"
                        f"<b>Title:</b> {title}\n"
                        f"<b>Status:</b> {status}\n"
                        f"<b>Priority:</b> {priority}\n"
                        f"<b>Created:</b> {created}\n\n"
                        f"{desc}"
                    )

                    # Add action buttons if still pending
                    reply_markup = None
                    if status == "pending":
                        reply_markup = {
                            "inline_keyboard": [
                                [
                                    {"text": "✅ Approve", "callback_data": f"approve:{request_id}"},
                                    {"text": "❌ Reject", "callback_data": f"reject:{request_id}"},
                                ]
                            ]
                        }

                    await self.send_message(
                        chat_id=chat_id,
                        text=text,
                        reply_markup=reply_markup,
                    )
                else:
                    await self.send_message(
                        chat_id=chat_id,
                        text=f"❓ Approval <code>{request_id}</code> not found.",
                    )
                return {"ok": True, "action": "details", "request_id": request_id}

            elif action == "rotate":
                # request_id carries the agent name for rotate callbacks
                agent_id = request_id
                await self.send_message(
                    chat_id=chat_id,
                    text=f"🔄 Rotating <b>{agent_id}</b>...",
                )
                try:
                    import asyncio as _asyncio

                    proc = await _asyncio.create_subprocess_exec(
                        "forge", "fleet", "swap", agent_id,
                        stdout=_asyncio.subprocess.PIPE,
                        stderr=_asyncio.subprocess.PIPE,
                    )
                    stdout, stderr = await _asyncio.wait_for(proc.communicate(), timeout=60.0)
                    if proc.returncode == 0:
                        await self.send_message(
                            chat_id=chat_id,
                            text=f"✅ <b>{agent_id}</b> rotated successfully",
                        )
                    else:
                        err = stderr.decode().strip()[:300] if stderr else "Unknown error"
                        await self.send_message(
                            chat_id=chat_id,
                            text=f"Rotation failed: {err}",
                        )
                    return {"ok": True, "action": "rotate", "agent_id": agent_id}
                except Exception as e:
                    await self.send_message(
                        chat_id=chat_id,
                        text=f"Rotation error: {e}",
                    )
                    return {"ok": True, "action": "rotate", "agent_id": agent_id, "error": str(e)}

        except Exception as e:
            logger.error(f"Callback processing failed: {e}")
            await self.send_message(
                chat_id=chat_id,
                text=f"⚠️ Error processing callback: {e}",
            )
            return {"ok": True, "action": action, "request_id": request_id, "error": str(e)}

        return {"ok": True, "handled": False}

    async def _cmd_start(self, chat_id: str, message: dict[str, Any]) -> dict[str, Any]:
        """Handle /start command."""
        first_name = message["from"].get("first_name", "there")

        welcome_text = f"""👋 Hello <b>{first_name}</b>!

Welcome to <b>FORGE Command Center</b> bot.

I can notify you about:
• Task completions
• Approval requests
• Error alerts
• Daily summaries

<b>Available commands:</b>
/status - Fleet status (all nodes + agents)
/status &lt;agent&gt; - Specific agent status
/dispatch &lt;agent&gt; &lt;task&gt; - Dispatch task to agent
/approve - List pending approvals
/approve &lt;id&gt; - Approve a request
/tasks - Show task queue
/spawn &lt;node&gt; &lt;agent&gt; - Spawn agent on a node
/stop &lt;agent&gt; - Stop a running agent
/nudge &lt;agent&gt; - Wake a stuck/idle agent
/rotate &lt;agent&gt; - Trigger context rotation
/help - Show this help message"""

        await self.send_message(chat_id=chat_id, text=welcome_text)
        return {"ok": True, "command": "start", "handled": True}

    async def _cmd_help(self, chat_id: str) -> dict[str, Any]:
        """Handle /help command."""
        help_text = """📚 <b>FORGE Command Center Bot</b>

<b>Commands:</b>
/start - Initialize bot
/status - Fleet status (all nodes + agents)
/status &lt;agent&gt; - Specific agent status
/dispatch &lt;agent&gt; &lt;task&gt; - Dispatch task to agent
/approve - List pending approvals
/approve &lt;id&gt; - Approve a request
/tasks - Show task queue
/spawn &lt;node&gt; &lt;agent&gt; - Spawn agent on a node
/stop &lt;agent&gt; - Stop a running agent
/nudge &lt;agent&gt; - Wake a stuck/idle agent
/rotate &lt;agent&gt; - Trigger context rotation
/help - Show this help

<b>Notification Types:</b>
✅ Task completion
🔘 Approval requests
❌ Error alerts
📊 Daily summaries"""

        await self.send_message(chat_id=chat_id, text=help_text)
        return {"ok": True, "command": "help", "handled": True}

    async def _cmd_status(self, chat_id: str, args: list[str]) -> dict[str, Any]:
        """Handle /status command — fetch real fleet summary."""
        try:
            # Lazy imports to avoid circular imports; fall back gracefully if CC is down.
            try:
                from forge_harness.webhook_server.api.fleet_summary import (
                    _get_health_summary,
                    _get_task_summary,
                    _shape_node_for_summary,
                )
                from forge_harness.webhook_server.api.nodes import _get_all_nodes

                raw_nodes = _get_all_nodes()
                shaped_nodes = [_shape_node_for_summary(n) for n in raw_nodes]
            except Exception:
                raw_nodes = []
                shaped_nodes = []

            if args:
                # Specific agent status
                agent_name = args[0].lower()
                for node in shaped_nodes:
                    for agent in node.get("agents", []):
                        if agent["name"].lower() == agent_name:
                            text = (
                                f"<b>{agent['name']}</b> on {node['name']}\n"
                                f"Status: {agent['status']}\n"
                                f"Context: {agent['context_pct']}%"
                            )
                            await self.send_message(chat_id=chat_id, text=text)
                            return {"ok": True, "command": "status", "handled": True}
                await self.send_message(
                    chat_id=chat_id,
                    text=f"Agent '{args[0]}' not found.",
                )
                return {"ok": True, "command": "status", "handled": True}

            # Fleet overview
            lines = ["<b>Fleet Status</b>\n"]
            for node in shaped_nodes:
                agents = node.get("agents", [])
                status_emoji = "🟢" if node.get("status") == "online" else "🔴"
                lines.append(f"{status_emoji} <b>{node['name']}</b> — {len(agents)} agents")
                for a in agents:
                    ctx = a.get("context_pct", 0)
                    ctx_bar = "🟢" if ctx < 50 else "🟡" if ctx < 80 else "🔴"
                    lines.append(f"  {ctx_bar} {a['name']}: {a['status']} ({ctx}%)")

            try:
                from forge_harness.webhook_server.api.fleet_summary import (
                    _get_task_summary,
                )
                task_summary = _get_task_summary()
            except Exception:
                task_summary = {"total": 0, "p0": 0, "p1": 0, "p2": 0}

            try:
                from forge_harness.webhook_server.api.fleet_summary import (
                    _get_health_summary,
                )
                health = _get_health_summary(raw_nodes)
            except Exception:
                health = {"uptime_hours": 0, "errors_1h": 0}

            lines.append(
                f"\nTasks: {task_summary.get('total', 0)} "
                f"(P0:{task_summary.get('p0', 0)} "
                f"P1:{task_summary.get('p1', 0)} "
                f"P2:{task_summary.get('p2', 0)})"
            )
            lines.append(
                f"Uptime: {health.get('uptime_hours', 0)}h | "
                f"Errors: {health.get('errors_1h', 0)}"
            )

            await self.send_message(chat_id=chat_id, text="\n".join(lines))
            return {"ok": True, "command": "status", "handled": True}

        except Exception as e:
            logger.error(f"Failed to get fleet status: {e}")
            await self.send_message(
                chat_id=chat_id,
                text=f"<b>Fleet Status</b>\n\nFailed to get fleet status: {e}",
            )
            return {"ok": True, "command": "status", "handled": True, "error": str(e)}

    async def _cmd_dispatch(self, chat_id: str, args: list[str]) -> dict[str, Any]:
        """Handle /dispatch command — dispatch task to an agent."""
        if len(args) < 2:
            await self.send_message(
                chat_id=chat_id,
                text="Usage: /dispatch <agent> <task description>\nExample: /dispatch glm update the README",
            )
            return {"ok": True, "command": "dispatch", "handled": True}

        agent = args[0]
        task_text = " ".join(args[1:])

        try:
            from forge_harness.openclaw.openclaw_gateway import _dispatch_to_agent

            dispatched = _dispatch_to_agent(agent, task_text)

            if dispatched:
                await self.send_message(
                    chat_id=chat_id,
                    text=f"Dispatched to <b>{agent}</b>:\n{task_text}",
                )
            else:
                await self.send_message(
                    chat_id=chat_id,
                    text=f"Failed to dispatch to <b>{agent}</b>. Agent may be offline or busy.",
                )

            return {"ok": True, "command": "dispatch", "dispatched": dispatched, "agent": agent}
        except Exception as e:
            logger.error(f"Dispatch failed: {e}")
            await self.send_message(chat_id=chat_id, text=f"Dispatch error: {e}")
            return {"ok": True, "command": "dispatch", "handled": True, "error": str(e)}

    async def _cmd_approve(self, chat_id: str, args: list[str]) -> dict[str, Any]:
        """Handle /approve command — list pending or approve specific request."""
        try:
            from forge_harness.webhook_server.handlers.approval_handler import get_approval_handler

            handler = get_approval_handler()

            if not args:
                # List pending approvals
                pending = await handler.list_pending(status="pending")
                if not pending:
                    await self.send_message(chat_id=chat_id, text="No pending approvals.")
                    return {"ok": True, "command": "approve", "handled": True}

                lines = ["<b>Pending Approvals</b>\n"]
                for req in pending[:10]:  # Max 10
                    rid = req.get("request_id", req.get("id", "?"))
                    title = req.get("title", "Untitled")
                    lines.append(f"• <code>{str(rid)[:8]}</code> — {title}")

                lines.append("\nUse /approve <id> to approve.")
                await self.send_message(chat_id=chat_id, text="\n".join(lines))
            else:
                request_id = args[0]
                await handler.approve_request(
                    request_id=request_id,
                    approver="telegram",
                )
                await self.send_message(
                    chat_id=chat_id,
                    text=f"Approved: <code>{request_id}</code>",
                )

            return {"ok": True, "command": "approve", "handled": True}
        except Exception as e:
            logger.error(f"Approve failed: {e}")
            await self.send_message(chat_id=chat_id, text=f"Error: {e}")
            return {"ok": True, "command": "approve", "handled": True, "error": str(e)}

    async def _cmd_tasks(self, chat_id: str, args: list[str]) -> dict[str, Any]:
        """Handle /tasks command — list tasks from queue."""
        try:
            from forge_harness.webhook_server.handlers.task_handler import get_task_handler

            handler = get_task_handler()
            tasks = await handler.list_tasks(limit=10)

            if not tasks:
                await self.send_message(chat_id=chat_id, text="No tasks in queue.")
                return {"ok": True, "command": "tasks", "handled": True}

            lines = ["<b>Task Queue</b>\n"]
            for t in tasks:
                tid = t.get("id", "?")
                title = (t.get("subject") or t.get("title", "Untitled"))[:50]
                status = t.get("status", "?")
                priority = t.get("priority", "?")
                emoji = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}.get(
                    priority, "⚪"
                )
                lines.append(f"{emoji} <code>{str(tid)[:8]}</code> [{status}] {title}")

            await self.send_message(chat_id=chat_id, text="\n".join(lines))
            return {"ok": True, "command": "tasks", "handled": True}
        except Exception as e:
            logger.error(f"Tasks failed: {e}")
            await self.send_message(chat_id=chat_id, text=f"Error: {e}")
            return {"ok": True, "command": "tasks", "handled": True, "error": str(e)}

    async def _cmd_spawn(self, chat_id: str, args: list[str]) -> dict[str, Any]:
        """Handle /spawn command — spawn an agent on a remote node."""
        if len(args) < 2:
            await self.send_message(
                chat_id=chat_id,
                text="Usage: /spawn &lt;node&gt; &lt;agent&gt;\nExample: /spawn sati opencode",
            )
            return {"ok": True, "command": "spawn", "handled": True}

        node, agent = args[0], args[1]
        valid_nodes = {"prya", "sati", "nova", "vega", "gaea"}
        if node not in valid_nodes:
            await self.send_message(
                chat_id=chat_id,
                text=f"Unknown node: {node}\nValid: {', '.join(sorted(valid_nodes))}",
            )
            return {"ok": True, "command": "spawn", "handled": True, "error": "invalid_node"}

        await self.send_message(
            chat_id=chat_id,
            text=f"Spawning <b>{agent}</b> on <b>{node}</b>...",
        )

        try:
            import asyncio as _asyncio

            proc = await _asyncio.create_subprocess_exec(
                "forge", "fleet", "spawn", agent, "--node", node,
                stdout=_asyncio.subprocess.PIPE,
                stderr=_asyncio.subprocess.PIPE,
            )
            stdout, stderr = await _asyncio.wait_for(proc.communicate(), timeout=30.0)

            if proc.returncode == 0:
                await self.send_message(
                    chat_id=chat_id,
                    text=f"<b>{agent}</b> spawned on <b>{node}</b>",
                )
            else:
                error_msg = stderr.decode().strip()[:300] if stderr else "Unknown error"
                await self.send_message(
                    chat_id=chat_id,
                    text=f"Failed to spawn: {error_msg}",
                )

            return {
                "ok": True,
                "command": "spawn",
                "node": node,
                "agent": agent,
                "returncode": proc.returncode,
            }
        except TimeoutError:
            await self.send_message(chat_id=chat_id, text="Spawn timed out after 30s")
            return {"ok": True, "command": "spawn", "error": "timeout"}
        except Exception as e:
            logger.error(f"Spawn failed: {e}")
            await self.send_message(chat_id=chat_id, text=f"Spawn error: {e}")
            return {"ok": True, "command": "spawn", "error": str(e)}

    async def _cmd_stop(self, chat_id: str, args: list[str]) -> dict[str, Any]:
        """Handle /stop command — stop a running agent."""
        if not args:
            await self.send_message(
                chat_id=chat_id,
                text="Usage: /stop &lt;agent&gt;\nExample: /stop glm",
            )
            return {"ok": True, "command": "stop", "handled": True}

        agent = args[0]
        await self.send_message(
            chat_id=chat_id,
            text=f"Stopping <b>{agent}</b>...",
        )

        try:
            import asyncio as _asyncio

            proc = await _asyncio.create_subprocess_exec(
                "forge", "fleet", "stop", agent,
                stdout=_asyncio.subprocess.PIPE,
                stderr=_asyncio.subprocess.PIPE,
            )
            stdout, stderr = await _asyncio.wait_for(proc.communicate(), timeout=30.0)

            if proc.returncode == 0:
                await self.send_message(
                    chat_id=chat_id,
                    text=f"<b>{agent}</b> stopped",
                )
            else:
                error_msg = stderr.decode().strip()[:300] if stderr else "Unknown error"
                await self.send_message(
                    chat_id=chat_id,
                    text=f"Failed to stop {agent}: {error_msg}",
                )

            return {
                "ok": True,
                "command": "stop",
                "agent": agent,
                "returncode": proc.returncode,
            }
        except TimeoutError:
            await self.send_message(chat_id=chat_id, text="Stop timed out after 30s")
            return {"ok": True, "command": "stop", "error": "timeout"}
        except Exception as e:
            logger.error(f"Stop failed: {e}")
            await self.send_message(chat_id=chat_id, text=f"Stop error: {e}")
            return {"ok": True, "command": "stop", "error": str(e)}

    async def _cmd_nudge(self, chat_id: str, args: list[str]) -> dict[str, Any]:
        """Handle /nudge command — send Enter keypress to wake a stuck/idle agent."""
        if not args:
            await self.send_message(
                chat_id=chat_id,
                text="Usage: /nudge &lt;agent&gt;\nExample: /nudge glm",
            )
            return {"ok": True, "command": "nudge", "handled": True}

        agent = args[0]

        try:
            import asyncio as _asyncio

            proc = await _asyncio.create_subprocess_exec(
                "tmux", "send-keys", "-t", f"forge:{agent}", "Enter",
                stdout=_asyncio.subprocess.PIPE,
                stderr=_asyncio.subprocess.PIPE,
            )
            stdout, stderr = await _asyncio.wait_for(proc.communicate(), timeout=30.0)

            if proc.returncode == 0:
                await self.send_message(
                    chat_id=chat_id,
                    text=f"Nudged <b>{agent}</b>",
                )
            else:
                error_msg = stderr.decode().strip()[:300] if stderr else "Unknown error"
                await self.send_message(
                    chat_id=chat_id,
                    text=f"Failed to nudge {agent}: {error_msg}",
                )

            return {
                "ok": True,
                "command": "nudge",
                "agent": agent,
                "returncode": proc.returncode,
            }
        except TimeoutError:
            await self.send_message(chat_id=chat_id, text="Nudge timed out after 30s")
            return {"ok": True, "command": "nudge", "error": "timeout"}
        except Exception as e:
            logger.error(f"Nudge failed: {e}")
            await self.send_message(chat_id=chat_id, text=f"Nudge error: {e}")
            return {"ok": True, "command": "nudge", "error": str(e)}

    async def _cmd_rotate(self, chat_id: str, args: list[str]) -> dict[str, Any]:
        """Handle /rotate command — trigger context rotation for an agent."""
        if not args:
            await self.send_message(
                chat_id=chat_id,
                text="Usage: /rotate &lt;agent&gt;\nExample: /rotate is.lead",
            )
            return {"ok": True, "command": "rotate", "handled": True}

        agent = args[0]
        await self.send_message(
            chat_id=chat_id,
            text=f"Rotating context for <b>{agent}</b>...",
        )

        try:
            import asyncio as _asyncio

            proc = await _asyncio.create_subprocess_exec(
                "forge", "fleet", "swap", agent,
                stdout=_asyncio.subprocess.PIPE,
                stderr=_asyncio.subprocess.PIPE,
            )
            stdout, stderr = await _asyncio.wait_for(proc.communicate(), timeout=30.0)

            if proc.returncode == 0:
                await self.send_message(
                    chat_id=chat_id,
                    text=f"Context rotation triggered for <b>{agent}</b>",
                )
            else:
                error_msg = stderr.decode().strip()[:300] if stderr else "Unknown error"
                await self.send_message(
                    chat_id=chat_id,
                    text=f"Failed to rotate {agent}: {error_msg}",
                )

            return {
                "ok": True,
                "command": "rotate",
                "agent": agent,
                "returncode": proc.returncode,
            }
        except TimeoutError:
            await self.send_message(chat_id=chat_id, text="Rotate timed out after 30s")
            return {"ok": True, "command": "rotate", "error": "timeout"}
        except Exception as e:
            logger.error(f"Rotate failed: {e}")
            await self.send_message(chat_id=chat_id, text=f"Rotate error: {e}")
            return {"ok": True, "command": "rotate", "error": str(e)}

    async def broadcast(
        self,
        text: str,
        tier: str = "phone",
        metadata: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Broadcast message to all authorized users.

        Args:
            text: Message text
            tier: Message tier
            metadata: Additional metadata

        Returns:
            List of results
        """
        if not self.allowed_users:
            logger.warning("No allowed users configured for broadcast")
            return []

        results = []
        for chat_id in self.allowed_users:
            try:
                result = await self.send_notification(
                    chat_id=chat_id,
                    title="FORGE Update",
                    message=text,
                    tier=tier,
                    metadata=metadata,
                )
                results.append({"chat_id": chat_id, "success": True, "result": result})
            except Exception as e:
                logger.error(f"Failed to send to {chat_id}: {e}")
                results.append({"chat_id": chat_id, "success": False, "error": str(e)})

        return results

    def get_bot_info(self) -> dict[str, Any]:
        """Get bot configuration info (for status checks)."""
        return {
            "configured": self.is_configured(),
            "has_token": bool(self.bot_token),
            "allowed_users_count": len(self.allowed_users),
            "has_webhook_secret": bool(self.webhook_secret),
            "authorized_chats": len(self._authorized_chats),
        }
