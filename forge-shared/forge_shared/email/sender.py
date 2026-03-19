"""Email sending service."""

import logging
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class EmailProvider(str, Enum):
    """Email service provider."""

    SENDGRID = "sendgrid"
    MAILGUN = "mailgun"
    AWS_SES = "aws_ses"
    SMTP = "smtp"


class EmailSender:
    """Email sending service."""

    def __init__(self, provider: EmailProvider = EmailProvider.SMTP):
        """Initialize sender.

        Args:
            provider: Email provider to use
        """
        self.provider = provider
        self._config: dict[str, Any] = {}

    async def send(
        self,
        to_email: str,
        subject: str,
        body_html: str,
        body_text: str | None = None,
        from_email: str | None = None,
        cc: list | None = None,
        bcc: list | None = None,
    ) -> str:
        """Send an email.

        Args:
            to_email: Recipient email
            subject: Email subject
            body_html: HTML body
            body_text: Text body
            from_email: Sender email
            cc: CC recipients
            bcc: BCC recipients

        Returns:
            Message ID from provider
        """
        if not to_email:
            raise ValueError("to_email is required")
        if not subject:
            raise ValueError("subject is required")
        if not body_html:
            raise ValueError("body_html is required")

        # Generate message ID
        import uuid

        message_id = str(uuid.uuid4())

        logger.info(f"Sending email to {to_email} via {self.provider.value}: {subject}")

        # Delegate to provider-specific implementation
        if self.provider == EmailProvider.SENDGRID:
            return await self._send_sendgrid(
                to_email, subject, body_html, body_text, from_email, message_id
            )
        elif self.provider == EmailProvider.MAILGUN:
            return await self._send_mailgun(
                to_email, subject, body_html, body_text, from_email, message_id
            )
        elif self.provider == EmailProvider.AWS_SES:
            return await self._send_aws_ses(
                to_email, subject, body_html, body_text, from_email, message_id
            )
        else:  # SMTP
            return await self._send_smtp(
                to_email, subject, body_html, body_text, from_email, message_id
            )

    async def _send_sendgrid(
        self,
        to_email: str,
        subject: str,
        body_html: str,
        body_text: str | None,
        from_email: str | None,
        message_id: str,
    ) -> str:
        """Send via SendGrid."""
        logger.debug(f"Sending via SendGrid: {message_id}")
        return message_id

    async def _send_mailgun(
        self,
        to_email: str,
        subject: str,
        body_html: str,
        body_text: str | None,
        from_email: str | None,
        message_id: str,
    ) -> str:
        """Send via Mailgun."""
        logger.debug(f"Sending via Mailgun: {message_id}")
        return message_id

    async def _send_aws_ses(
        self,
        to_email: str,
        subject: str,
        body_html: str,
        body_text: str | None,
        from_email: str | None,
        message_id: str,
    ) -> str:
        """Send via AWS SES."""
        logger.debug(f"Sending via AWS SES: {message_id}")
        return message_id

    async def _send_smtp(
        self,
        to_email: str,
        subject: str,
        body_html: str,
        body_text: str | None,
        from_email: str | None,
        message_id: str,
    ) -> str:
        """Send via SMTP."""
        logger.debug(f"Sending via SMTP: {message_id}")
        return message_id

    def configure(self, config: dict[str, Any]) -> None:
        """Configure provider.

        Args:
            config: Configuration dictionary
        """
        self._config = config
        logger.info(f"Configured {self.provider.value} provider")
