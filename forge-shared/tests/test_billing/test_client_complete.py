"""
Comprehensive tests for billing/client.py
Target: 30% → 80%+ coverage (biggest impact to reach 80% overall)
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timezone
import stripe
from stripe import StripeError

from forge_shared.billing.client import StripeClient
from forge_shared.billing.models import (
    BillingError,
    CheckoutSession,
    Customer,
    PricingTier,
    Subscription,
    SubscriptionStatus,
)


class TestStripeClientInit:
    """Test StripeClient initialization"""
    
    def test_init_with_api_key(self):
        """Test initialization with API key"""
        client = StripeClient(api_key="sk_test_123")
        assert client.api_key == "sk_test_123"
        assert client.webhook_secret is None
        assert stripe.api_key == "sk_test_123"
    
    def test_init_with_webhook_secret(self):
        """Test initialization with webhook secret"""
        client = StripeClient(
            api_key="sk_test_123",
            webhook_secret="whsec_456"
        )
        assert client.api_key == "sk_test_123"
        assert client.webhook_secret == "whsec_456"


class TestCreateCheckoutSession:
    """Test create_checkout_session method"""
    
    @pytest.fixture
    def client(self):
        """Create StripeClient instance"""
        return StripeClient(api_key="sk_test_123")
    
    @pytest.mark.asyncio
    async def test_create_checkout_session_success(self, client):
        """Test successful checkout session creation"""
        mock_session = Mock()
        mock_session.id = "cs_test_123"
        mock_session.url = "https://checkout.stripe.com/test"
        mock_session.customer = "cus_123"
        mock_session.expires_at = 1234567890
        
        with patch('stripe.checkout.Session.create', return_value=mock_session):
            session = await client.create_checkout_session(
                product="interview-simulator",
                tier="pro",
                user_id="user_123",
                success_url="https://example.com/success",
                cancel_url="https://example.com/cancel"
            )
            
            assert isinstance(session, CheckoutSession)
            assert session.id == "cs_test_123"
            assert session.url == "https://checkout.stripe.com/test"
            assert session.product == "interview-simulator"
            assert session.tier == PricingTier.PRO
            assert session.user_id == "user_123"
    
    @pytest.mark.asyncio
    async def test_create_checkout_session_with_tier_enum(self, client):
        """Test checkout session with PricingTier enum"""
        mock_session = Mock()
        mock_session.id = "cs_test_123"
        mock_session.url = "https://checkout.stripe.com/test"
        mock_session.customer = None
        mock_session.expires_at = None
        
        with patch('stripe.checkout.Session.create', return_value=mock_session):
            session = await client.create_checkout_session(
                product="interview-simulator",
                tier=PricingTier.PRO,
                user_id="user_123",
                success_url="https://example.com/success",
                cancel_url="https://example.com/cancel"
            )
            
            assert session.tier == PricingTier.PRO
    
    @pytest.mark.asyncio
    async def test_create_checkout_session_with_customer_email(self, client):
        """Test checkout session with customer email"""
        mock_session = Mock()
        mock_session.id = "cs_test_123"
        mock_session.url = "https://checkout.stripe.com/test"
        mock_session.customer = None
        mock_session.expires_at = None
        
        with patch('stripe.checkout.Session.create', return_value=mock_session) as mock_create:
            await client.create_checkout_session(
                product="interview-simulator",
                tier="pro",
                user_id="user_123",
                success_url="https://example.com/success",
                cancel_url="https://example.com/cancel",
                customer_email="test@example.com"
            )
            
            call_kwargs = mock_create.call_args[1]
            assert call_kwargs['customer_email'] == "test@example.com"
    
    @pytest.mark.asyncio
    async def test_create_checkout_session_with_customer_id(self, client):
        """Test checkout session with existing customer ID"""
        mock_session = Mock()
        mock_session.id = "cs_test_123"
        mock_session.url = "https://checkout.stripe.com/test"
        mock_session.customer = "cus_123"
        mock_session.expires_at = None
        
        with patch('stripe.checkout.Session.create', return_value=mock_session) as mock_create:
            await client.create_checkout_session(
                product="interview-simulator",
                tier="pro",
                user_id="user_123",
                success_url="https://example.com/success",
                cancel_url="https://example.com/cancel",
                customer_id="cus_123"
            )
            
            call_kwargs = mock_create.call_args[1]
            assert call_kwargs['customer'] == "cus_123"
    
    @pytest.mark.asyncio
    async def test_create_checkout_session_with_metadata(self, client):
        """Test checkout session with custom metadata"""
        mock_session = Mock()
        mock_session.id = "cs_test_123"
        mock_session.url = "https://checkout.stripe.com/test"
        mock_session.customer = None
        mock_session.expires_at = None
        
        with patch('stripe.checkout.Session.create', return_value=mock_session) as mock_create:
            await client.create_checkout_session(
                product="interview-simulator",
                tier="pro",
                user_id="user_123",
                success_url="https://example.com/success",
                cancel_url="https://example.com/cancel",
                metadata={"custom": "data"}
            )
            
            call_kwargs = mock_create.call_args[1]
            assert call_kwargs['metadata']['custom'] == "data"
    
    @pytest.mark.asyncio
    async def test_create_checkout_session_invalid_product(self, client):
        """Test checkout session with invalid product"""
        with pytest.raises(BillingError, match="Invalid product"):
            await client.create_checkout_session(
                product="invalid-product",
                tier="pro",
                user_id="user_123",
                success_url="https://example.com/success",
                cancel_url="https://example.com/cancel"
            )
    
    @pytest.mark.asyncio
    async def test_create_checkout_session_invalid_tier(self, client):
        """Test checkout session with invalid tier"""
        # Invalid tier string causes ValueError when converting to PricingTier enum
        with pytest.raises((BillingError, ValueError)):
            await client.create_checkout_session(
                product="interview-simulator",
                tier="invalid-tier",
                user_id="user_123",
                success_url="https://example.com/success",
                cancel_url="https://example.com/cancel"
            )
    
    @pytest.mark.asyncio
    async def test_create_checkout_session_free_tier(self, client):
        """Test checkout session with free tier"""
        with pytest.raises(BillingError, match="Free tier does not require checkout"):
            await client.create_checkout_session(
                product="interview-simulator",
                tier="free",
                user_id="user_123",
                success_url="https://example.com/success",
                cancel_url="https://example.com/cancel"
            )
    
    @pytest.mark.asyncio
    async def test_create_checkout_session_stripe_error(self, client):
        """Test checkout session handling Stripe errors"""
        with patch('stripe.checkout.Session.create', side_effect=StripeError("API error")):
            with pytest.raises(BillingError, match="Failed to create checkout session"):
                await client.create_checkout_session(
                    product="interview-simulator",
                    tier="pro",
                    user_id="user_123",
                    success_url="https://example.com/success",
                    cancel_url="https://example.com/cancel"
                )


class TestCreateCustomer:
    """Test create_customer method"""
    
    @pytest.fixture
    def client(self):
        """Create StripeClient instance"""
        return StripeClient(api_key="sk_test_123")
    
    @pytest.mark.asyncio
    async def test_create_customer_success(self, client):
        """Test successful customer creation"""
        mock_customer = Mock()
        mock_customer.id = "cus_123"
        mock_customer.created = 1234567890
        
        with patch('stripe.Customer.create', return_value=mock_customer):
            customer = await client.create_customer(
                user_id="user_123",
                email="test@example.com"
            )
            
            assert isinstance(customer, Customer)
            assert customer.id == "cus_123"
            assert customer.email == "test@example.com"
            assert customer.user_id == "user_123"
    
    @pytest.mark.asyncio
    async def test_create_customer_with_name(self, client):
        """Test customer creation with name"""
        mock_customer = Mock()
        mock_customer.id = "cus_123"
        mock_customer.created = 1234567890
        
        with patch('stripe.Customer.create', return_value=mock_customer) as mock_create:
            await client.create_customer(
                user_id="user_123",
                email="test@example.com",
                name="John Doe"
            )
            
            call_kwargs = mock_create.call_args[1]
            assert call_kwargs['name'] == "John Doe"
    
    @pytest.mark.asyncio
    async def test_create_customer_with_metadata(self, client):
        """Test customer creation with custom metadata"""
        mock_customer = Mock()
        mock_customer.id = "cus_123"
        mock_customer.created = 1234567890
        
        with patch('stripe.Customer.create', return_value=mock_customer) as mock_create:
            await client.create_customer(
                user_id="user_123",
                email="test@example.com",
                metadata={"company": "ACME"}
            )
            
            call_kwargs = mock_create.call_args[1]
            assert call_kwargs['metadata']['company'] == "ACME"
    
    @pytest.mark.asyncio
    async def test_create_customer_stripe_error(self, client):
        """Test customer creation handling Stripe errors"""
        with patch('stripe.Customer.create', side_effect=StripeError("API error")):
            with pytest.raises(BillingError, match="Failed to create customer"):
                await client.create_customer(
                    user_id="user_123",
                    email="test@example.com"
                )


class TestGetSubscription:
    """Test get_subscription method"""
    
    @pytest.fixture
    def client(self):
        """Create StripeClient instance"""
        return StripeClient(api_key="sk_test_123")
    
    @pytest.mark.asyncio
    async def test_get_subscription_success(self, client):
        """Test successful subscription retrieval"""
        mock_sub = Mock()
        mock_sub.id = "sub_123"
        mock_sub.status = "active"
        mock_sub.customer = "cus_123"
        mock_sub.metadata = {
            "product": "interview-simulator",
            "tier": "pro",
            "user_id": "user_123"
        }
        mock_sub.current_period_start = 1234567890
        mock_sub.current_period_end = 1234657890
        mock_sub.cancel_at_period_end = False
        mock_sub.canceled_at = None
        
        with patch('stripe.Subscription.retrieve', return_value=mock_sub):
            subscription = await client.get_subscription("sub_123")
            
            assert isinstance(subscription, Subscription)
            assert subscription.id == "sub_123"
            assert subscription.status == SubscriptionStatus.ACTIVE
            assert subscription.product == "interview-simulator"
    
    @pytest.mark.asyncio
    async def test_get_subscription_not_found(self, client):
        """Test subscription not found"""
        with patch('stripe.Subscription.retrieve', side_effect=stripe.InvalidRequestError("Not found", "id")):
            subscription = await client.get_subscription("sub_invalid")
            assert subscription is None
    
    @pytest.mark.asyncio
    async def test_get_subscription_stripe_error(self, client):
        """Test subscription retrieval handling Stripe errors"""
        with patch('stripe.Subscription.retrieve', side_effect=StripeError("API error")):
            with pytest.raises(BillingError, match="Failed to retrieve subscription"):
                await client.get_subscription("sub_123")


class TestCancelSubscription:
    """Test cancel_subscription method"""
    
    @pytest.fixture
    def client(self):
        """Create StripeClient instance"""
        return StripeClient(api_key="sk_test_123")
    
    @pytest.mark.asyncio
    async def test_cancel_at_period_end(self, client):
        """Test canceling subscription at period end"""
        with patch('stripe.Subscription.modify') as mock_modify:
            result = await client.cancel_subscription("sub_123", cancel_at_period_end=True)
            
            assert result is True
            mock_modify.assert_called_once_with(
                "sub_123",
                cancel_at_period_end=True
            )
    
    @pytest.mark.asyncio
    async def test_cancel_immediately(self, client):
        """Test canceling subscription immediately"""
        with patch('stripe.Subscription.cancel') as mock_cancel:
            result = await client.cancel_subscription("sub_123", cancel_at_period_end=False)
            
            assert result is True
            mock_cancel.assert_called_once_with("sub_123")
    
    @pytest.mark.asyncio
    async def test_cancel_subscription_stripe_error(self, client):
        """Test subscription cancellation handling Stripe errors"""
        with patch('stripe.Subscription.modify', side_effect=StripeError("API error")):
            with pytest.raises(BillingError, match="Failed to cancel subscription"):
                await client.cancel_subscription("sub_123")


class TestGetCustomerSubscriptions:
    """Test get_customer_subscriptions method"""
    
    @pytest.fixture
    def client(self):
        """Create StripeClient instance"""
        return StripeClient(api_key="sk_test_123")
    
    @pytest.mark.asyncio
    async def test_get_customer_subscriptions_success(self, client):
        """Test getting customer subscriptions"""
        mock_sub = Mock()
        mock_sub.id = "sub_123"
        mock_sub.status = "active"
        mock_sub.customer = "cus_123"
        mock_sub.metadata = {
            "product": "interview-simulator",
            "tier": "pro",
            "user_id": "user_123"
        }
        mock_sub.current_period_start = 1234567890
        mock_sub.current_period_end = 1234657890
        mock_sub.cancel_at_period_end = False
        mock_sub.canceled_at = None
        
        mock_list = Mock()
        mock_list.data = [mock_sub]
        
        with patch('stripe.Subscription.list', return_value=mock_list):
            subscriptions = await client.get_customer_subscriptions("cus_123")
            
            assert len(subscriptions) == 1
            assert subscriptions[0].id == "sub_123"
    
    @pytest.mark.asyncio
    async def test_get_customer_subscriptions_active_only(self, client):
        """Test getting active subscriptions only"""
        mock_list = Mock()
        mock_list.data = []
        
        with patch('stripe.Subscription.list', return_value=mock_list) as mock_list_call:
            await client.get_customer_subscriptions("cus_123", active_only=True)
            
            call_kwargs = mock_list_call.call_args[1]
            assert call_kwargs['status'] == "active"
    
    @pytest.mark.asyncio
    async def test_get_customer_subscriptions_all(self, client):
        """Test getting all subscriptions"""
        mock_list = Mock()
        mock_list.data = []
        
        with patch('stripe.Subscription.list', return_value=mock_list) as mock_list_call:
            await client.get_customer_subscriptions("cus_123", active_only=False)
            
            call_kwargs = mock_list_call.call_args[1]
            assert 'status' not in call_kwargs
    
    @pytest.mark.asyncio
    async def test_get_customer_subscriptions_stripe_error(self, client):
        """Test getting customer subscriptions handling Stripe errors"""
        with patch('stripe.Subscription.list', side_effect=StripeError("API error")):
            with pytest.raises(BillingError, match="Failed to retrieve customer subscriptions"):
                await client.get_customer_subscriptions("cus_123")


class TestGetCustomerByUserId:
    """Test get_customer_by_user_id method"""
    
    @pytest.fixture
    def client(self):
        """Create StripeClient instance"""
        return StripeClient(api_key="sk_test_123")
    
    @pytest.mark.asyncio
    async def test_get_customer_by_user_id_found(self, client):
        """Test finding customer by user ID"""
        mock_customer = Mock()
        mock_customer.id = "cus_123"
        mock_customer.email = "test@example.com"
        mock_customer.name = "John Doe"
        mock_customer.created = 1234567890
        
        mock_search = Mock()
        mock_search.data = [mock_customer]
        
        with patch('stripe.Customer.search', return_value=mock_search):
            customer = await client.get_customer_by_user_id("user_123")
            
            assert customer is not None
            assert customer.id == "cus_123"
            assert customer.email == "test@example.com"
            assert customer.user_id == "user_123"
    
    @pytest.mark.asyncio
    async def test_get_customer_by_user_id_not_found(self, client):
        """Test customer not found by user ID"""
        mock_search = Mock()
        mock_search.data = []
        
        with patch('stripe.Customer.search', return_value=mock_search):
            customer = await client.get_customer_by_user_id("user_123")
            assert customer is None
    
    @pytest.mark.asyncio
    async def test_get_customer_by_user_id_stripe_error(self, client):
        """Test getting customer by user ID handling Stripe errors"""
        with patch('stripe.Customer.search', side_effect=StripeError("API error")):
            customer = await client.get_customer_by_user_id("user_123")
            assert customer is None


class TestParseSubscription:
    """Test _parse_subscription helper method"""
    
    @pytest.fixture
    def client(self):
        """Create StripeClient instance"""
        return StripeClient(api_key="sk_test_123")
    
    def test_parse_subscription_success(self, client):
        """Test parsing Stripe subscription"""
        mock_sub = Mock()
        mock_sub.id = "sub_123"
        mock_sub.status = "active"
        mock_sub.customer = "cus_123"
        mock_sub.metadata = {
            "product": "interview-simulator",
            "tier": "pro",
            "user_id": "user_123"
        }
        mock_sub.current_period_start = 1234567890
        mock_sub.current_period_end = 1234657890
        mock_sub.cancel_at_period_end = False
        mock_sub.canceled_at = None
        
        subscription = client._parse_subscription(mock_sub)
        
        assert subscription is not None
        assert subscription.id == "sub_123"
        assert subscription.status == SubscriptionStatus.ACTIVE
        assert subscription.tier == PricingTier.PRO
    
    def test_parse_subscription_invalid_tier(self, client):
        """Test parsing subscription with invalid tier"""
        mock_sub = Mock()
        mock_sub.id = "sub_123"
        mock_sub.status = "active"
        mock_sub.customer = "cus_123"
        mock_sub.metadata = {"tier": "invalid"}
        mock_sub.current_period_start = None
        mock_sub.current_period_end = None
        mock_sub.cancel_at_period_end = False
        mock_sub.canceled_at = None
        
        subscription = client._parse_subscription(mock_sub)
        
        assert subscription is not None
        assert subscription.tier == PricingTier.FREE
    
    def test_parse_subscription_invalid_status(self, client):
        """Test parsing subscription with invalid status"""
        mock_sub = Mock()
        mock_sub.id = "sub_123"
        mock_sub.status = "invalid"
        mock_sub.customer = "cus_123"
        mock_sub.metadata = {}
        mock_sub.current_period_start = None
        mock_sub.current_period_end = None
        mock_sub.cancel_at_period_end = False
        mock_sub.canceled_at = None
        
        subscription = client._parse_subscription(mock_sub)
        
        assert subscription is not None
        assert subscription.status == SubscriptionStatus.CANCELED


class TestStripeClientIntegration:
    """Integration tests for StripeClient"""
    
    @pytest.fixture
    def client(self):
        """Create StripeClient instance"""
        return StripeClient(api_key="sk_test_123")
    
    @pytest.mark.asyncio
    async def test_full_subscription_flow(self, client):
        """Test complete subscription lifecycle"""
        # Mock customer creation
        mock_customer = Mock()
        mock_customer.id = "cus_123"
        mock_customer.created = 1234567890
        
        # Mock checkout session
        mock_session = Mock()
        mock_session.id = "cs_test_123"
        mock_session.url = "https://checkout.stripe.com/test"
        mock_session.customer = "cus_123"
        mock_session.expires_at = None
        
        # Mock subscription
        mock_sub = Mock()
        mock_sub.id = "sub_123"
        mock_sub.status = "active"
        mock_sub.customer = "cus_123"
        mock_sub.metadata = {
            "product": "interview-simulator",
            "tier": "pro",
            "user_id": "user_123"
        }
        mock_sub.current_period_start = 1234567890
        mock_sub.current_period_end = 1234657890
        mock_sub.cancel_at_period_end = False
        mock_sub.canceled_at = None
        
        with patch('stripe.Customer.create', return_value=mock_customer), \
             patch('stripe.checkout.Session.create', return_value=mock_session), \
             patch('stripe.Subscription.retrieve', return_value=mock_sub), \
             patch('stripe.Subscription.modify'):
            
            # Create customer
            customer = await client.create_customer(
                user_id="user_123",
                email="test@example.com"
            )
            assert customer.id == "cus_123"
            
            # Create checkout session
            session = await client.create_checkout_session(
                product="interview-simulator",
                tier="pro",
                user_id="user_123",
                success_url="https://example.com/success",
                cancel_url="https://example.com/cancel",
                customer_id="cus_123"
            )
            assert session.id == "cs_test_123"
            
            # Get subscription
            subscription = await client.get_subscription("sub_123")
            assert subscription.id == "sub_123"
            
            # Cancel subscription
            result = await client.cancel_subscription("sub_123")
            assert result is True
