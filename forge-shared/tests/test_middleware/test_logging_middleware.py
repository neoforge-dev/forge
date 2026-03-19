"""
Comprehensive tests for StructuredLoggingMiddleware.

Covers request/response logging, error handling, sanitization,
performance tracking, and edge cases.
"""

import json
import logging
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import Request

from forge_shared.middleware.logging import (
    StructuredLoggingMiddleware,
    JSONFormatter,
    LoggingMiddleware,
    create_logging_middleware,
)


class TestStructuredLoggingMiddlewareInit:
    """Tests for StructuredLoggingMiddleware initialization."""

    def test_init_with_defaults(self):
        """Test middleware initialization with default values."""
        app = MagicMock()
        middleware = StructuredLoggingMiddleware(app)

        assert middleware.service_name == "forge-shared"
        assert middleware.environment in ["development", "staging", "production"]
        assert "/health" in middleware.exclude_paths
        assert "authorization" in middleware.exclude_headers
        assert "password" in middleware.sanitize_fields
        assert middleware.include_request_body is False
        assert middleware.include_response_body is False

    def test_init_with_custom_params(self):
        """Test middleware with custom parameters."""
        app = MagicMock()
        middleware = StructuredLoggingMiddleware(
            app,
            service_name="test-service",
            service_version="1.0.0",
            log_level="DEBUG",
            environment="production",
            exclude_paths=["/custom-exclude"],
            exclude_headers=["x-custom-secret"],
            sanitize_fields=["custom_field"],
            include_request_body=True,
            include_response_body=True,
            performance_threshold_ms=500.0,
        )

        assert middleware.service_name == "test-service"
        assert middleware.service_version == "1.0.0"
        assert middleware.environment == "production"

    def test_logger_creation(self):
        """Test logger is created correctly."""
        app = MagicMock()
        middleware = StructuredLoggingMiddleware(app, service_name="test-api")

        assert middleware.logger is not None
        assert middleware.logger.name == "forge.test-api"


class TestRequestProcessing:
    """Tests for request processing and logging."""

    @pytest.mark.asyncio
    async def test_dispatch_skips_excluded_paths(self):
        """Test that excluded paths are not logged."""
        app = MagicMock()
        middleware = StructuredLoggingMiddleware(
            app,
            exclude_paths=["/health"]
        )

        request = MagicMock(spec=Request)
        request.url = MagicMock()
        request.url.path = "/health"
        call_next = AsyncMock(return_value=MagicMock(status_code=200))

        response = await middleware.dispatch(request, call_next)

        # Should call next but not log (no logger.info calls expected)
        assert response.status_code == 200
        call_next.assert_called_once()

    @pytest.mark.asyncio
    async def test_dispatch_logs_success_request(self):
        """Test successful request is logged."""
        app = MagicMock()
        middleware = StructuredLoggingMiddleware(app)

        # Create a mock request
        request = MagicMock(spec=Request)
        request.url = MagicMock()
        request.url.path = "/api/test"
        request.url.query = ""
        request.method = "GET"
        request.headers = MagicMock()
        request.headers.items = MagicMock(return_value=[])
        request.headers.get = MagicMock(return_value=None)
        request.client = MagicMock(host="192.168.1.1")

        call_next = AsyncMock(return_value=MagicMock(status_code=200, headers=MagicMock()))

        response = await middleware.dispatch(request, call_next)

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_dispatch_logs_error_response(self):
        """Test error responses are logged correctly."""
        app = MagicMock()
        middleware = StructuredLoggingMiddleware(app)

        request = MagicMock(spec=Request)
        request.url = MagicMock()
        request.url.path = "/api/error"
        request.url.query = ""
        request.method = "GET"
        request.headers = MagicMock()
        request.headers.items = MagicMock(return_value=[])
        request.headers.get = MagicMock(return_value=None)
        request.client = MagicMock(host="127.0.0.1")

        call_next = AsyncMock(return_value=MagicMock(status_code=500, headers=MagicMock()))

        response = await middleware.dispatch(request, call_next)

        assert response.status_code == 500

    @pytest.mark.asyncio
    async def test_dispatch_handles_exceptions(self):
        """Test exception handling during request processing."""
        app = MagicMock()
        middleware = StructuredLoggingMiddleware(app)

        request = MagicMock(spec=Request)
        request.url = MagicMock()
        request.url.path = "/api/test"
        request.headers = MagicMock()
        request.headers.items = MagicMock(return_value=[])
        request.headers.get = MagicMock(return_value=None)
        request.client = MagicMock(host="127.0.0.1")

        async def raise_error(request):
            raise ValueError("Test error")

        call_next = raise_error

        with pytest.raises(ValueError):
            await middleware.dispatch(request, call_next)


class TestClientIPExtraction:
    """Tests for client IP extraction."""

    def test_get_client_ip_from_forwarded_header(self):
        """Test extracting IP from X-Forwarded-For header."""
        app = MagicMock()
        middleware = StructuredLoggingMiddleware(app)

        request = MagicMock(spec=Request)
        request.headers = MagicMock()
        request.headers.get = MagicMock(side_effect=lambda k, d=None: {
            "X-Forwarded-For": "203.0.113.1, 198.51.100.1",
            "X-Real-IP": None
        }.get(k, d))
        request.client = MagicMock(host="192.168.1.1")

        ip = middleware._get_client_ip(request)

        assert ip == "203.0.113.1"

    def test_get_client_ip_from_real_ip_header(self):
        """Test extracting IP from X-Real-IP header."""
        app = MagicMock()
        middleware = StructuredLoggingMiddleware(app)

        request = MagicMock(spec=Request)
        request.headers = MagicMock()
        request.headers.get = MagicMock(side_effect=lambda k, d=None: {
            "X-Forwarded-For": None,
            "X-Real-IP": "203.0.113.50"
        }.get(k, d))
        request.client = MagicMock(host="192.168.1.1")

        ip = middleware._get_client_ip(request)

        assert ip == "203.0.113.50"

    def test_get_client_ip_from_direct_connection(self):
        """Test extracting IP from direct connection."""
        app = MagicMock()
        middleware = StructuredLoggingMiddleware(app)

        request = MagicMock(spec=Request)
        request.headers = MagicMock()
        request.headers.get = MagicMock(return_value=None)
        request.client = MagicMock(host="192.168.1.100")

        ip = middleware._get_client_ip(request)

        assert ip == "192.168.1.100"

    def test_get_client_ip_no_client(self):
        """Test IP extraction when client is None."""
        app = MagicMock()
        middleware = StructuredLoggingMiddleware(app)

        request = MagicMock(spec=Request)
        request.headers = MagicMock()
        request.headers.get = MagicMock(return_value=None)
        request.client = None

        ip = middleware._get_client_ip(request)

        assert ip is None


class TestSanitization:
    """Tests for data sanitization."""

    def test_sanitize_dict_with_sensitive_fields(self):
        """Test sanitizing dictionaries with sensitive fields."""
        app = MagicMock()
        middleware = StructuredLoggingMiddleware(
            app,
            sanitize_fields=["password", "token", "secret"]
        )

        data = {
            "username": "john",
            "password": "secret123",
            "token": "abc123xyz",
            "public_field": "visible"
        }

        sanitized = middleware._sanitize(data)

        assert sanitized["username"] == "john"
        assert sanitized["password"] == "<REDACTED>"
        assert sanitized["token"] == "<REDACTED>"
        assert sanitized["public_field"] == "visible"

    def test_sanitize_nested_dict(self):
        """Test sanitizing nested dictionaries."""
        app = MagicMock()
        middleware = StructuredLoggingMiddleware(
            app,
            sanitize_fields=["password"]
        )

        data = {
            "user": {
                "name": "John",
                "credentials": {
                    "password": "secret123"
                }
            }
        }

        sanitized = middleware._sanitize(data)

        assert sanitized["user"]["name"] == "John"
        assert sanitized["user"]["credentials"]["password"] == "<REDACTED>"

    def test_sanitize_list(self):
        """Test sanitizing lists of dictionaries."""
        app = MagicMock()
        middleware = StructuredLoggingMiddleware(
            app,
            sanitize_fields=["secret"]
        )

        data = [
            {"name": "Item 1", "secret": "hidden1"},
            {"name": "Item 2", "secret": "hidden2"}
        ]

        sanitized = middleware._sanitize(data)

        assert sanitized[0]["secret"] == "<REDACTED>"
        assert sanitized[1]["secret"] == "<REDACTED>"

    def test_sanitize_case_insensitive(self):
        """Test that sanitization is case-insensitive."""
        app = MagicMock()
        middleware = StructuredLoggingMiddleware(
            app,
            sanitize_fields=["password"]
        )

        data = {
            "Password": "upper",
            "PASSWORD": "upper2",
            "password": "lower"
        }

        sanitized = middleware._sanitize(data)

        assert sanitized["Password"] == "<REDACTED>"
        assert sanitized["PASSWORD"] == "<REDACTED>"
        assert sanitized["password"] == "<REDACTED>"

    def test_sanitize_preserves_non_sensitive_data(self):
        """Test that non-sensitive data is preserved."""
        app = MagicMock()
        middleware = StructuredLoggingMiddleware(
            app,
            sanitize_fields=["secret"]
        )

        data = {
            "name": "John",
            "email": "john@example.com",
            "age": 30,
            "active": True
        }

        sanitized = middleware._sanitize(data)

        assert sanitized == data


class TestJSONFormatter:
    """Tests for JSON log formatter."""

    def test_formatter_creates_valid_json(self):
        """Test formatter produces valid JSON."""
        formatter = JSONFormatter("test-service", "1.0.0")

        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test message",
            args=(),
            exc_info=None,
        )

        formatted = formatter.format(record)

        assert isinstance(formatted, str)
        parsed = json.loads(formatted)
        assert parsed["message"] == "Test message"

    def test_formatter_includes_service_metadata(self):
        """Test formatter includes service metadata."""
        formatter = JSONFormatter(
            service_name="my-service",
            service_version="2.0.0",
            environment="production"
        )

        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test",
            args=(),
            exc_info=None,
        )

        formatted = formatter.format(record)
        parsed = json.loads(formatted)

        assert parsed["service"] == "my-service"
        assert parsed["version"] == "2.0.0"
        assert parsed["environment"] == "production"

    def test_formatter_includes_timestamp(self):
        """Test formatter includes ISO timestamp."""
        formatter = JSONFormatter("test-service", "1.0.0")

        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test",
            args=(),
            exc_info=None,
        )

        formatted = formatter.format(record)
        parsed = json.loads(formatted)

        assert "timestamp" in parsed
        assert "T" in parsed["timestamp"]  # ISO format

    def test_formatter_includes_exception_info(self):
        """Test formatter includes exception information."""
        formatter = JSONFormatter("test-service", "1.0.0")

        try:
            raise ValueError("Test exception")
        except ValueError:
            import sys
            exc_info = sys.exc_info()

        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname="test.py",
            lineno=1,
            msg="Error occurred",
            args=(),
            exc_info=exc_info,
        )

        formatted = formatter.format(record)
        parsed = json.loads(formatted)

        assert "exception" in parsed


class TestConvenienceFunctions:
    """Tests for convenience functions."""

    def test_create_logging_middleware(self):
        """Test create_logging_middleware convenience function."""
        middleware = create_logging_middleware(
            service_name="test-api",
            log_level="DEBUG",
            environment="staging"
        )

        assert isinstance(middleware, StructuredLoggingMiddleware)
        assert middleware.service_name == "test-api"
        assert middleware.environment == "staging"

    def test_logging_middleware_alias(self):
        """Test LoggingMiddleware alias points to StructuredLoggingMiddleware."""
        assert LoggingMiddleware == StructuredLoggingMiddleware


class TestShouldSkip:
    """Tests for _should_skip method."""

    def test_should_skip_with_excluded_path(self):
        """Test _should_skip returns True for excluded paths."""
        app = MagicMock()
        middleware = StructuredLoggingMiddleware(
            app,
            exclude_paths=["/health", "/metrics"]
        )

        request = MagicMock(spec=Request)
        request.url = MagicMock()
        request.url.path = "/health"

        assert middleware._should_skip(request) is True

    def test_should_skip_with_non_excluded_path(self):
        """Test _should_skip returns False for normal paths."""
        app = MagicMock()
        middleware = StructuredLoggingMiddleware(
            app,
            exclude_paths=["/health"]
        )

        request = MagicMock(spec=Request)
        request.url = MagicMock()
        request.url.path = "/api/test"

        assert middleware._should_skip(request) is False


class TestGetCorrelationId:
    """Tests for _get_correlation_id method."""

    def test_get_correlation_id_from_header(self):
        """Test getting correlation ID from header."""
        app = MagicMock()
        middleware = StructuredLoggingMiddleware(app)

        request = MagicMock(spec=Request)
        request.headers = MagicMock()
        request.headers.get = MagicMock(return_value="existing-id-123")

        correlation_id = middleware._get_correlation_id(request)

        assert correlation_id == "existing-id-123"

    def test_get_correlation_id_generates_new(self):
        """Test generating new correlation ID when none exists."""
        app = MagicMock()
        middleware = StructuredLoggingMiddleware(app)

        request = MagicMock(spec=Request)
        request.headers = MagicMock()
        request.headers.get = MagicMock(return_value=None)

        correlation_id = middleware._get_correlation_id(request)

        # Should generate a UUID
        assert correlation_id is not None
        assert len(correlation_id) == 36  # UUID format

    def test_get_correlation_id_from_request_id_fallback(self):
        """Test using X-Request-ID as fallback."""
        app = MagicMock()
        middleware = StructuredLoggingMiddleware(app)

        request = MagicMock(spec=Request)
        request.headers = MagicMock()
        request.headers.get = MagicMock(side_effect=lambda k, d=None: {
            "X-Correlation-ID": None,
            "X-Request-ID": "request-id-456"
        }.get(k, d))

        correlation_id = middleware._get_correlation_id(request)

        assert correlation_id == "request-id-456"


class TestPrepareRequestData:
    """Tests for _prepare_request_data method."""

    def test_prepare_request_data_basic(self):
        """Test basic request data preparation."""
        app = MagicMock()
        middleware = StructuredLoggingMiddleware(app)

        request = MagicMock(spec=Request)
        request.url = MagicMock()
        request.url.path = "/api/test"
        request.url.query = "param=value"
        request.method = "POST"
        request.headers = MagicMock()
        request.headers.items = MagicMock(return_value=[("content-type", "application/json")])
        request.headers.get = MagicMock(return_value=None)
        request.client = MagicMock(host="192.168.1.1")

        data = middleware._prepare_request_data(request)

        assert data["event"] == "request"
        assert data["method"] == "POST"
        assert data["path"] == "/api/test"
        assert data["query_string"] == "param=value"

    def test_prepare_request_data_excludes_sensitive_headers(self):
        """Test sensitive headers are excluded."""
        app = MagicMock()
        middleware = StructuredLoggingMiddleware(
            app,
            exclude_headers=["authorization", "cookie"]
        )

        request = MagicMock(spec=Request)
        request.url = MagicMock()
        request.url.path = "/api/test"
        request.url.query = ""
        request.method = "GET"
        request.headers = MagicMock()
        request.headers.items = MagicMock(return_value=[
            ("authorization", "Bearer token"),
            ("content-type", "application/json"),
            ("cookie", "session=abc")
        ])
        request.headers.get = MagicMock(return_value=None)
        request.client = MagicMock(host="127.0.0.1")

        data = middleware._prepare_request_data(request)

        # Sensitive headers should be excluded
        assert "authorization" not in data.get("headers", {})
        assert "cookie" not in data.get("headers", {})


class TestPrepareResponseData:
    """Tests for _prepare_response_data method."""

    def test_prepare_response_data_basic(self):
        """Test basic response data preparation."""
        app = MagicMock()
        middleware = StructuredLoggingMiddleware(app)

        response = MagicMock(status_code=200, headers=MagicMock())
        response.headers.items = MagicMock(return_value=[])

        data = middleware._prepare_response_data(response, 150.5)

        assert data["event"] == "response"
        assert data["status_code"] == 200
        assert data["duration_ms"] == 150.5
        assert "timestamp" in data

    def test_prepare_response_data_slow_request(self):
        """Test slow request detection."""
        app = MagicMock()
        middleware = StructuredLoggingMiddleware(
            app,
            performance_threshold_ms=100.0
        )

        response = MagicMock(status_code=200, headers=MagicMock())
        response.headers.items = MagicMock(return_value=[])

        data = middleware._prepare_response_data(response, 150.0)

        assert data["slow_request"] is True

    def test_prepare_response_data_fast_request(self):
        """Test fast request is not marked as slow."""
        app = MagicMock()
        middleware = StructuredLoggingMiddleware(
            app,
            performance_threshold_ms=1000.0
        )

        response = MagicMock(status_code=200, headers=MagicMock())
        response.headers.items = MagicMock(return_value=[])

        data = middleware._prepare_response_data(response, 50.0)

        assert data["slow_request"] is False
