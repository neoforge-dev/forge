"""
Stripe webhook handling utilities.

Provides functions for verifying and processing Stripe webhook events
across the FORGE portfolio.

Example:
    ```python
    from forge_shared.billing.webhooks import handle_webhook, verify_signature

    @app.post("/webhooks/stripe")
    async def stripe_webhook(request: Request):
        payload = await request.body()
        signature = request.headers.get("stripe-signature", "")

        try:
            event = handle_webhook(payload, signature, webhook_secret)
            if event.type == "checkout.session.completed":
                # Handle successful checkout
                pass
            return {"received": True}
        except BillingError as e:
            raise HTTPException(status_code=400, detail=str(e))
    ```
"""

from datetime import datetime, timezone
from typing import Any

import stripe
from stripe import SignatureVerificationError

from forge_shared.billing.models import BillingError, WebhookEvent


HANDLED_EVENTS = frozenset(
    [
        "checkout.session.completed",
        "checkout.session.expired",
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
        "customer.subscription.paused",
        "customer.subscription.resumed",
        "invoice.paid",
        "invoice.payment_failed",
        "invoice.payment_action_required",
        "customer.created",
        "customer.updated",
        "customer.deleted",
    ]
)


def verify_signature(payload: bytes, signature: str, secret: str) -> bool:
    """
    Verify Stripe webhook signature.

    Args:
        payload: Raw request body bytes
        signature: Stripe-Signature header value
        secret: Webhook signing secret (whsec_...)

    Returns:
        True if signature is valid, False otherwise

    Example:
        ```python
        is_valid = verify_signature(
            payload=request_body,
            signature=request.headers["stripe-signature"],
            secret=webhook_secret
        )
        if not is_valid:
            raise HTTPException(status_code=401)
        ```
    """
    try:
        stripe.Webhook.construct_event(payload, signature, secret)  # type: ignore[no-untyped-call]
        return True
    except (SignatureVerificationError, ValueError):
        return False


def handle_webhook(
    payload: bytes,
    signature: str,
    secret: str,
) -> WebhookEvent:
    """
    Verify and parse Stripe webhook event.

    Verifies the webhook signature and constructs a WebhookEvent from
    the payload. This is the main entry point for webhook processing.

    Args:
        payload: Raw request body bytes
        signature: Stripe-Signature header value
        secret: Webhook signing secret (whsec_...)

    Returns:
        WebhookEvent with parsed event data

    Raises:
        BillingError: If signature verification fails or payload is invalid

    Example:
        ```python
        from fastapi import Request, HTTPException
        from forge_shared.billing import handle_webhook

        @app.post("/webhooks/stripe")
        async def stripe_webhook(request: Request):
            payload = await request.body()
            signature = request.headers.get("stripe-signature", "")

            try:
                event = handle_webhook(payload, signature, WEBHOOK_SECRET)

                if event.type == "checkout.session.completed":
                    await handle_checkout_completed(event.data)
                elif event.type == "customer.subscription.deleted":
                    await handle_subscription_deleted(event.data)

                return {"received": True}
            except BillingError as e:
                raise HTTPException(status_code=400, detail=str(e))
        ```
    """
    try:
        event = stripe.Webhook.construct_event(payload, signature, secret)  # type: ignore[no-untyped-call]
    except SignatureVerificationError as e:
        raise BillingError(
            message="Invalid webhook signature",
            code="invalid_signature",
            details={"error": str(e)},
        ) from e
    except ValueError as e:
        raise BillingError(
            message="Invalid webhook payload",
            code="invalid_payload",
            details={"error": str(e)},
        ) from e

    event_data = _extract_event_data(event)

    return WebhookEvent(
        id=event.id,
        type=event.type,
        data=event_data,
        processed=False,
        created_at=datetime.fromtimestamp(event.created, tz=timezone.utc)
        if event.created
        else None,
    )


def _extract_event_data(event: stripe.Event) -> dict[str, Any]:
    """
    Extract relevant data from Stripe event.

    Normalizes event data for easier processing downstream.
    """
    data: dict[str, Any] = {}
    obj = event.data.object

    if event.type.startswith("checkout.session"):
        data = _extract_checkout_session_data(obj)
    elif event.type.startswith("customer.subscription"):
        data = _extract_subscription_data(obj)
    elif event.type.startswith("invoice"):
        data = _extract_invoice_data(obj)
    elif event.type.startswith("customer."):
        data = _extract_customer_data(obj)

    data["raw"] = dict(obj) if hasattr(obj, "__iter__") else {}

    return data


def _extract_checkout_session_data(session: Any) -> dict[str, Any]:
    """Extract data from checkout session object."""
    metadata = getattr(session, "metadata", {}) or {}
    return {
        "session_id": getattr(session, "id", ""),
        "customer_id": getattr(session, "customer", ""),
        "subscription_id": getattr(session, "subscription", ""),
        "customer_email": getattr(session, "customer_email", ""),
        "payment_status": getattr(session, "payment_status", ""),
        "status": getattr(session, "status", ""),
        "product": metadata.get("product", ""),
        "tier": metadata.get("tier", ""),
        "user_id": metadata.get("user_id", ""),
    }


def _extract_subscription_data(subscription: Any) -> dict[str, Any]:
    """Extract data from subscription object."""
    metadata = getattr(subscription, "metadata", {}) or {}
    return {
        "subscription_id": getattr(subscription, "id", ""),
        "customer_id": getattr(subscription, "customer", ""),
        "status": getattr(subscription, "status", ""),
        "cancel_at_period_end": getattr(subscription, "cancel_at_period_end", False),
        "current_period_end": getattr(subscription, "current_period_end", None),
        "canceled_at": getattr(subscription, "canceled_at", None),
        "product": metadata.get("product", ""),
        "tier": metadata.get("tier", ""),
        "user_id": metadata.get("user_id", ""),
    }


def _extract_invoice_data(invoice: Any) -> dict[str, Any]:
    """Extract data from invoice object."""
    return {
        "invoice_id": getattr(invoice, "id", ""),
        "customer_id": getattr(invoice, "customer", ""),
        "subscription_id": getattr(invoice, "subscription", ""),
        "status": getattr(invoice, "status", ""),
        "amount_paid": getattr(invoice, "amount_paid", 0),
        "amount_due": getattr(invoice, "amount_due", 0),
        "currency": getattr(invoice, "currency", ""),
        "payment_intent": getattr(invoice, "payment_intent", ""),
        "hosted_invoice_url": getattr(invoice, "hosted_invoice_url", ""),
    }


def _extract_customer_data(customer: Any) -> dict[str, Any]:
    """Extract data from customer object."""
    metadata = getattr(customer, "metadata", {}) or {}
    return {
        "customer_id": getattr(customer, "id", ""),
        "email": getattr(customer, "email", ""),
        "name": getattr(customer, "name", ""),
        "user_id": metadata.get("user_id", ""),
    }


def is_handled_event(event_type: str) -> bool:
    """Check if event type is handled by the billing module."""
    return event_type in HANDLED_EVENTS
