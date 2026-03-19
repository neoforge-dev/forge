"""
Stripe client for billing operations.

Provides a unified client for Stripe operations including checkout sessions,
subscriptions, and customer management across the FORGE portfolio.

Example:
    ```python
    from forge_shared.billing import StripeClient

    client = StripeClient(api_key="sk_test_...")
    session = await client.create_checkout_session(
        product="interview-simulator",
        tier="pro",
        user_id="user_123",
        success_url="https://app.example.com/success",
        cancel_url="https://app.example.com/cancel"
    )
    print(session.url)
    ```
"""

from datetime import UTC, datetime
from typing import Any

import stripe
from stripe import StripeError

from forge_shared.billing.config import get_price_id, is_valid_product, is_valid_tier
from forge_shared.billing.models import (
    BillingError,
    CheckoutSession,
    Customer,
    Invoice,
    PaymentIntent,
    Price,
    PricingTier,
    Product,
    Subscription,
    SubscriptionStatus,
)


class StripeClient:
    """
    Async-compatible Stripe client for billing operations.

    Provides methods for creating checkout sessions, managing subscriptions,
    and handling customers. All methods are designed to work in async contexts
    though Stripe's library uses synchronous I/O.

    Attributes:
        api_key: Stripe API secret key
        webhook_secret: Stripe webhook signing secret (optional)

    Example:
        ```python
        client = StripeClient(
            api_key="sk_test_...",
            webhook_secret="whsec_..."
        )

        # Create checkout session
        session = await client.create_checkout_session(
            product="interview-simulator",
            tier="pro",
            user_id="user_123",
            success_url="https://example.com/success",
            cancel_url="https://example.com/cancel"
        )

        # Get subscription
        sub = await client.get_subscription("sub_123")
        ```
    """

    def __init__(
        self,
        api_key: str,
        webhook_secret: str | None = None,
    ) -> None:
        """
        Initialize Stripe client.

        Args:
            api_key: Stripe API secret key (sk_test_... or sk_live_...)
            webhook_secret: Stripe webhook signing secret (whsec_...)
        """
        self.api_key = api_key
        self.webhook_secret = webhook_secret
        stripe.api_key = api_key

    async def create_checkout_session(
        self,
        product: str,
        tier: str | PricingTier,
        user_id: str,
        success_url: str,
        cancel_url: str,
        customer_id: str | None = None,
        customer_email: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> CheckoutSession:
        """
        Create a Stripe Checkout session.

        Args:
            product: FORGE product identifier (e.g., "interview-simulator")
            tier: Pricing tier (e.g., "pro" or PricingTier.PRO)
            user_id: Internal user identifier
            success_url: URL to redirect on successful payment
            cancel_url: URL to redirect on canceled payment
            customer_id: Existing Stripe customer ID (optional)
            customer_email: Customer email for new customers (optional)
            metadata: Additional metadata for the session (optional)

        Returns:
            CheckoutSession with session ID and checkout URL

        Raises:
            BillingError: If checkout session creation fails

        Example:
            ```python
            session = await client.create_checkout_session(
                product="interview-simulator",
                tier=PricingTier.PRO,
                user_id="user_123",
                success_url="https://app.codeswiftr.com/success?session_id={CHECKOUT_SESSION_ID}",
                cancel_url="https://app.codeswiftr.com/pricing"
            )
            # Redirect user to session.url
            ```
        """
        tier_enum = PricingTier(tier) if isinstance(tier, str) else tier
        tier_str = tier_enum.value

        if not is_valid_product(product):
            raise BillingError(
                message=f"Invalid product: {product}",
                code="invalid_product",
            )

        if not is_valid_tier(product, tier_str):
            raise BillingError(
                message=f"Invalid tier '{tier_str}' for product '{product}'",
                code="invalid_tier",
            )

        price_id = get_price_id(product, tier_str)
        if price_id is None:
            raise BillingError(
                message="Free tier does not require checkout",
                code="free_tier_no_checkout",
            )

        session_metadata = {
            "product": product,
            "tier": tier_str,
            "user_id": user_id,
            **(metadata or {}),
        }

        session_params: dict[str, Any] = {
            "mode": "subscription",
            "line_items": [{"price": price_id, "quantity": 1}],
            "success_url": success_url,
            "cancel_url": cancel_url,
            "metadata": session_metadata,
            "subscription_data": {"metadata": session_metadata},
        }

        if customer_id:
            session_params["customer"] = customer_id
        elif customer_email:
            session_params["customer_email"] = customer_email

        try:
            session = stripe.checkout.Session.create(**session_params)

            return CheckoutSession(
                id=session.id,
                url=session.url or "",
                product=product,
                tier=tier_enum,
                user_id=user_id,
                customer_id=session.customer if isinstance(session.customer, str) else None,
                expires_at=datetime.fromtimestamp(session.expires_at, tz=UTC)
                if session.expires_at
                else None,
            )
        except StripeError as e:
            raise BillingError(
                message=f"Failed to create checkout session: {e}",
                code="checkout_creation_failed",
                details={"stripe_error": str(e)},
            ) from e

    async def create_customer(
        self,
        user_id: str = "",
        email: str = "",
        name: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> Customer:
        """
        Create a Stripe customer.

        Args:
            user_id: Internal user identifier
            email: Customer email address
            name: Customer name (optional)
            metadata: Additional metadata (optional)

        Returns:
            Customer with Stripe customer ID

        Raises:
            BillingError: If customer creation fails
        """
        customer_metadata = {"user_id": user_id, **(metadata or {})}

        try:
            create_params: dict[str, Any] = {
                "email": email,
                "metadata": customer_metadata,
            }
            if name:
                create_params["name"] = name
            customer = stripe.Customer.create(**create_params)

            return Customer(
                id=customer.id,
                email=email,
                user_id=user_id,
                name=name,
                created_at=datetime.fromtimestamp(customer.created, tz=UTC)
                if customer.created
                else None,
            )
        except StripeError as e:
            raise BillingError(
                message=f"Failed to create customer: {e}",
                code="customer_creation_failed",
                details={"stripe_error": str(e)},
            ) from e

    async def get_subscription(self, subscription_id: str) -> Subscription | None:
        """
        Get subscription by ID.

        Args:
            subscription_id: Stripe subscription ID

        Returns:
            Subscription if found, None otherwise

        Raises:
            BillingError: If retrieval fails (except not found)
        """
        try:
            sub = stripe.Subscription.retrieve(subscription_id)
            return self._parse_subscription(sub)
        except stripe.InvalidRequestError:
            return None
        except StripeError as e:
            raise BillingError(
                message=f"Failed to retrieve subscription: {e}",
                code="subscription_retrieval_failed",
                details={"stripe_error": str(e)},
            ) from e

    async def cancel_subscription(
        self,
        subscription_id: str,
        cancel_at_period_end: bool = True,
    ) -> bool:
        """
        Cancel a subscription.

        Args:
            subscription_id: Stripe subscription ID
            cancel_at_period_end: If True, cancel at end of billing period;
                                  if False, cancel immediately

        Returns:
            True if cancellation successful

        Raises:
            BillingError: If cancellation fails
        """
        try:
            if cancel_at_period_end:
                stripe.Subscription.modify(
                    subscription_id,
                    cancel_at_period_end=True,
                )
            else:
                stripe.Subscription.cancel(subscription_id)
            return True
        except StripeError as e:
            raise BillingError(
                message=f"Failed to cancel subscription: {e}",
                code="subscription_cancellation_failed",
                details={"stripe_error": str(e)},
            ) from e

    async def get_customer_subscriptions(
        self,
        customer_id: str,
        active_only: bool = True,
    ) -> list[Subscription]:
        """
        Get all subscriptions for a customer.

        Args:
            customer_id: Stripe customer ID
            active_only: If True, only return active subscriptions

        Returns:
            List of subscriptions

        Raises:
            BillingError: If retrieval fails
        """
        try:
            params: dict[str, Any] = {"customer": customer_id}
            if active_only:
                params["status"] = "active"

            subscriptions = stripe.Subscription.list(**params)

            return [
                sub
                for sub in (self._parse_subscription(s) for s in subscriptions.data)
                if sub is not None
            ]
        except StripeError as e:
            raise BillingError(
                message=f"Failed to retrieve customer subscriptions: {e}",
                code="subscriptions_retrieval_failed",
                details={"stripe_error": str(e)},
            ) from e

    async def get_customer_by_user_id(self, user_id: str) -> Customer | None:
        """
        Find customer by internal user ID.

        Args:
            user_id: Internal user identifier

        Returns:
            Customer if found, None otherwise
        """
        try:
            customers = stripe.Customer.search(
                query=f"metadata['user_id']:'{user_id}'",
            )

            if not customers.data:
                return None

            customer = customers.data[0]
            return Customer(
                id=customer.id,
                email=customer.email or "",
                user_id=user_id,
                name=customer.name,
                created_at=datetime.fromtimestamp(customer.created, tz=UTC)
                if customer.created
                else None,
            )
        except StripeError:
            return None

    async def get_customer(self, customer_id: str) -> Customer:
        """
        Retrieve a Stripe customer by ID.

        Args:
            customer_id: Stripe customer ID

        Returns:
            Customer object

        Raises:
            BillingError: If retrieval fails
        """
        try:
            raw = stripe.Customer.retrieve(customer_id)
            return Customer(
                id=raw.id,
                email=raw.get("email") or "",
                user_id=raw.get("metadata", {}).get("user_id", "") if raw.get("metadata") else "",
                name=raw.get("name"),
                created_at=datetime.fromtimestamp(raw.created, tz=UTC)
                if raw.get("created")
                else None,
            )
        except StripeError as e:
            raise BillingError(
                message=f"Failed to retrieve customer: {e}",
                code="customer_retrieval_failed",
                details={"stripe_error": str(e)},
            ) from e

    async def update_customer(self, customer_id: str, **kwargs: Any) -> Customer:
        """
        Update a Stripe customer.

        Args:
            customer_id: Stripe customer ID
            **kwargs: Fields to update (e.g., email, name, metadata)

        Returns:
            Updated Customer object

        Raises:
            BillingError: If update fails
        """
        try:
            raw = stripe.Customer.modify(customer_id, **kwargs)
            return Customer(
                id=raw.id,
                email=raw.get("email") or "",
                user_id=raw.get("metadata", {}).get("user_id", "") if raw.get("metadata") else "",
                name=raw.get("name"),
                created_at=datetime.fromtimestamp(raw.created, tz=UTC)
                if raw.get("created")
                else None,
            )
        except StripeError as e:
            raise BillingError(
                message=f"Failed to update customer: {e}",
                code="customer_update_failed",
                details={"stripe_error": str(e)},
            ) from e

    async def delete_customer(self, customer_id: str) -> bool:
        """
        Delete a Stripe customer.

        Args:
            customer_id: Stripe customer ID

        Returns:
            True if deletion was successful

        Raises:
            BillingError: If deletion fails
        """
        try:
            stripe.Customer.delete(customer_id)
            return True
        except StripeError as e:
            raise BillingError(
                message=f"Failed to delete customer: {e}",
                code="customer_deletion_failed",
                details={"stripe_error": str(e)},
            ) from e

    async def create_subscription(
        self,
        customer_id: str,
        price_id: str,
        **kwargs: Any,
    ) -> Subscription:
        """
        Create a Stripe subscription.

        Args:
            customer_id: Stripe customer ID
            price_id: Stripe price ID
            **kwargs: Additional subscription parameters

        Returns:
            Subscription object

        Raises:
            BillingError: If creation fails
        """
        try:
            raw = stripe.Subscription.create(
                customer=customer_id,
                items=[{"price": price_id}],
                **kwargs,
            )
            parsed = self._parse_subscription(raw)
            if parsed is None:
                raise BillingError(
                    message="Failed to parse subscription response",
                    code="subscription_parse_failed",
                )
            return parsed
        except BillingError:
            raise
        except StripeError as e:
            raise BillingError(
                message=f"Failed to create subscription: {e}",
                code="subscription_creation_failed",
                details={"stripe_error": str(e)},
            ) from e

    async def update_subscription(
        self,
        subscription_id: str,
        **kwargs: Any,
    ) -> Subscription:
        """
        Update a Stripe subscription.

        Args:
            subscription_id: Stripe subscription ID
            **kwargs: Fields to update (e.g., price_id)

        Returns:
            Updated Subscription object

        Raises:
            BillingError: If update fails
        """
        try:
            raw = stripe.Subscription.modify(subscription_id, **kwargs)
            parsed = self._parse_subscription(raw)
            if parsed is None:
                raise BillingError(
                    message="Failed to parse subscription response",
                    code="subscription_parse_failed",
                )
            return parsed
        except BillingError:
            raise
        except StripeError as e:
            raise BillingError(
                message=f"Failed to update subscription: {e}",
                code="subscription_update_failed",
                details={"stripe_error": str(e)},
            ) from e

    async def get_checkout_session(self, session_id: str) -> CheckoutSession:
        """
        Retrieve a Stripe checkout session by ID.

        Args:
            session_id: Stripe checkout session ID

        Returns:
            CheckoutSession object

        Raises:
            BillingError: If retrieval fails
        """
        try:
            raw = stripe.checkout.Session.retrieve(session_id)
            metadata = raw.get("metadata") or {}
            product = metadata.get("product", "")
            tier_str = metadata.get("tier", "free")
            user_id = metadata.get("user_id", "")
            try:
                tier = PricingTier(tier_str)
            except ValueError:
                tier = PricingTier.FREE
            return CheckoutSession(
                id=raw.id,
                url=raw.url or "",
                product=product,
                tier=tier,
                user_id=user_id,
                customer_id=raw.customer if isinstance(raw.customer, str) else None,
                expires_at=datetime.fromtimestamp(raw.expires_at, tz=UTC)
                if raw.expires_at
                else None,
            )
        except StripeError as e:
            raise BillingError(
                message=f"Failed to retrieve checkout session: {e}",
                code="checkout_retrieval_failed",
                details={"stripe_error": str(e)},
            ) from e

    async def create_payment_intent(
        self,
        amount: int,
        currency: str = "usd",
        customer_id: str | None = None,
        **kwargs: Any,
    ) -> PaymentIntent:
        """
        Create a Stripe payment intent.

        Args:
            amount: Amount in smallest currency unit (e.g., cents)
            currency: ISO 4217 currency code
            customer_id: Stripe customer ID (optional)
            **kwargs: Additional payment intent parameters

        Returns:
            PaymentIntent object

        Raises:
            BillingError: If creation fails
        """
        try:
            params: dict[str, Any] = {
                "amount": amount,
                "currency": currency,
                **kwargs,
            }
            if customer_id:
                params["customer"] = customer_id

            raw = stripe.PaymentIntent.create(**params)
            return PaymentIntent(
                id=raw.id,
                amount=raw.amount,
                currency=raw.currency,
                status=raw.status,
                customer_id=raw.customer if isinstance(raw.customer, str) else None,
            )
        except StripeError as e:
            raise BillingError(
                message=f"Failed to create payment intent: {e}",
                code="payment_intent_creation_failed",
                details={"stripe_error": str(e)},
            ) from e

    async def get_payment_intent(self, intent_id: str) -> PaymentIntent:
        """
        Retrieve a Stripe payment intent by ID.

        Args:
            intent_id: Stripe payment intent ID

        Returns:
            PaymentIntent object

        Raises:
            BillingError: If retrieval fails
        """
        try:
            raw = stripe.PaymentIntent.retrieve(intent_id)
            return PaymentIntent(
                id=raw.id,
                amount=raw.amount,
                currency=raw.currency,
                status=raw.status,
                customer_id=raw.customer if isinstance(raw.customer, str) else None,
            )
        except StripeError as e:
            raise BillingError(
                message=f"Failed to retrieve payment intent: {e}",
                code="payment_intent_retrieval_failed",
                details={"stripe_error": str(e)},
            ) from e

    async def list_invoices(
        self,
        customer_id: str | None = None,
        **kwargs: Any,
    ) -> list[Invoice]:
        """
        List Stripe invoices.

        Args:
            customer_id: Stripe customer ID (optional)
            **kwargs: Additional filter parameters

        Returns:
            List of Invoice objects

        Raises:
            BillingError: If retrieval fails
        """
        try:
            params: dict[str, Any] = {**kwargs}
            if customer_id:
                params["customer"] = customer_id

            raw_list = stripe.Invoice.list(**params)
            return [
                Invoice(
                    id=inv.id,
                    customer_id=inv.customer if isinstance(inv.customer, str) else None,
                    amount_due=inv.amount_due or 0,
                    status=inv.status or "draft",
                )
                for inv in raw_list.data
            ]
        except StripeError as e:
            raise BillingError(
                message=f"Failed to list invoices: {e}",
                code="invoice_list_failed",
                details={"stripe_error": str(e)},
            ) from e

    async def get_invoice(self, invoice_id: str) -> Invoice:
        """
        Retrieve a Stripe invoice by ID.

        Args:
            invoice_id: Stripe invoice ID

        Returns:
            Invoice object

        Raises:
            BillingError: If retrieval fails
        """
        try:
            raw = stripe.Invoice.retrieve(invoice_id)
            return Invoice(
                id=raw.id,
                customer_id=raw.customer if isinstance(raw.customer, str) else None,
                amount_due=raw.amount_due or 0,
                status=raw.status or "draft",
            )
        except StripeError as e:
            raise BillingError(
                message=f"Failed to retrieve invoice: {e}",
                code="invoice_retrieval_failed",
                details={"stripe_error": str(e)},
            ) from e

    async def list_products(self, **kwargs: Any) -> list[Product]:
        """
        List Stripe products.

        Args:
            **kwargs: Filter parameters (e.g., active=True)

        Returns:
            List of Product objects

        Raises:
            BillingError: If retrieval fails
        """
        try:
            raw_list = stripe.Product.list(**kwargs)
            return [
                Product(
                    id=prod.id,
                    name=prod.name or "",
                    active=prod.active if prod.active is not None else True,
                )
                for prod in raw_list.data
            ]
        except StripeError as e:
            raise BillingError(
                message=f"Failed to list products: {e}",
                code="product_list_failed",
                details={"stripe_error": str(e)},
            ) from e

    async def get_product(self, product_id: str) -> Product:
        """
        Retrieve a Stripe product by ID.

        Args:
            product_id: Stripe product ID

        Returns:
            Product object

        Raises:
            BillingError: If retrieval fails
        """
        try:
            raw = stripe.Product.retrieve(product_id)
            return Product(
                id=raw.id,
                name=raw.name or "",
                active=raw.active if raw.active is not None else True,
            )
        except StripeError as e:
            raise BillingError(
                message=f"Failed to retrieve product: {e}",
                code="product_retrieval_failed",
                details={"stripe_error": str(e)},
            ) from e

    async def list_prices(
        self,
        product_id: str | None = None,
        **kwargs: Any,
    ) -> list[Price]:
        """
        List Stripe prices.

        Args:
            product_id: Stripe product ID to filter by (optional)
            **kwargs: Additional filter parameters

        Returns:
            List of Price objects

        Raises:
            BillingError: If retrieval fails
        """
        try:
            params: dict[str, Any] = {**kwargs}
            if product_id:
                params["product"] = product_id

            raw_list = stripe.Price.list(**params)
            return [
                Price(
                    id=p.id,
                    product_id=p.product if isinstance(p.product, str) else "",
                    unit_amount=p.unit_amount or 0,
                    currency=p.currency or "usd",
                )
                for p in raw_list.data
            ]
        except StripeError as e:
            raise BillingError(
                message=f"Failed to list prices: {e}",
                code="price_list_failed",
                details={"stripe_error": str(e)},
            ) from e

    async def get_price(self, price_id: str) -> Price:
        """
        Retrieve a Stripe price by ID.

        Args:
            price_id: Stripe price ID

        Returns:
            Price object

        Raises:
            BillingError: If retrieval fails
        """
        try:
            raw = stripe.Price.retrieve(price_id)
            return Price(
                id=raw.id,
                product_id=raw.product if isinstance(raw.product, str) else "",
                unit_amount=raw.unit_amount or 0,
                currency=raw.currency or "usd",
            )
        except StripeError as e:
            raise BillingError(
                message=f"Failed to retrieve price: {e}",
                code="price_retrieval_failed",
                details={"stripe_error": str(e)},
            ) from e

    def _parse_subscription(self, sub: stripe.Subscription) -> Subscription | None:
        """Parse Stripe subscription object to internal model."""
        metadata = sub.metadata or {}
        product = metadata.get("product", "")
        tier_str = metadata.get("tier", "free")
        user_id = metadata.get("user_id", "")

        try:
            tier = PricingTier(tier_str)
        except ValueError:
            tier = PricingTier.FREE

        try:
            status = SubscriptionStatus(sub.status)
        except ValueError:
            status = SubscriptionStatus.CANCELED

        customer_id = sub.customer if isinstance(sub.customer, str) else ""

        period_start = getattr(sub, "current_period_start", None)
        period_end = getattr(sub, "current_period_end", None)

        return Subscription(
            id=sub.id,
            status=status,
            product=product,
            tier=tier,
            user_id=user_id,
            customer_id=customer_id,
            current_period_start=datetime.fromtimestamp(period_start, tz=UTC)
            if period_start
            else None,
            current_period_end=datetime.fromtimestamp(period_end, tz=UTC) if period_end else None,
            cancel_at_period_end=sub.cancel_at_period_end or False,
            canceled_at=datetime.fromtimestamp(sub.canceled_at, tz=UTC)
            if sub.canceled_at
            else None,
        )
