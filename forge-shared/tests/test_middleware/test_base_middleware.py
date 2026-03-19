"""Tests for BaseMiddleware class."""

import pytest
from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient
from starlette.middleware.base import RequestResponseEndpoint

from forge_shared.middleware.base import BaseMiddleware


class CustomMiddleware(BaseMiddleware):
    """Custom middleware for testing."""

    async def _process_request(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        """Process request with custom logic."""
        response = await super()._process_request(request, call_next)
        response.headers["X-Custom-Header"] = "test-value"
        return response


class TestBaseMiddleware:
    """Test BaseMiddleware functionality."""

    def test_middleware_initialization(self) -> None:
        """Test middleware initialization."""
        app = FastAPI()
        middleware = BaseMiddleware(app)

        assert middleware.log_requests is False
        assert middleware.log_responses is False
        assert middleware.include_request_id is True

    def test_middleware_with_logging(self) -> None:
        """Test middleware with logging enabled."""
        app = FastAPI()
        middleware = BaseMiddleware(
            app,
            log_requests=True,
            log_responses=True,
        )

        assert middleware.log_requests is True
        assert middleware.log_responses is True

    def test_request_id_extraction(self) -> None:
        """Test request ID extraction."""
        app = FastAPI()
        middleware = BaseMiddleware(app)

        # Mock request with request ID
        request = Request(
            scope={
                "type": "http",
                "method": "GET",
                "headers": [],
                "query_string": b"",
                "path": "/",
            },
        )
        request.state.request_id = "test-request-id"

        request_id = middleware._get_request_id(request)
        assert request_id == "test-request-id"

    def test_request_id_not_present(self) -> None:
        """Test request ID when not present."""
        app = FastAPI()
        middleware = BaseMiddleware(app)

        request = Request(
            scope={
                "type": "http",
                "method": "GET",
                "headers": [],
                "query_string": b"",
                "path": "/",
            },
        )

        request_id = middleware._get_request_id(request)
        assert request_id is None

    def test_add_response_header(self) -> None:
        """Test adding header to response."""
        app = FastAPI()
        middleware = BaseMiddleware(app)

        response = Response(content="test")
        middleware._add_response_header(response, "X-Test-Header", "test-value")

        assert response.headers["X-Test-Header"] == "test-value"

    def test_get_response_header(self) -> None:
        """Test getting header from response."""
        app = FastAPI()
        middleware = BaseMiddleware(app)

        response = Response(content="test")
        response.headers["X-Test-Header"] = "test-value"

        header_value = middleware._get_response_header(response, "X-Test-Header")
        assert header_value == "test-value"

    def test_get_response_header_default(self) -> None:
        """Test getting non-existent header with default."""
        app = FastAPI()
        middleware = BaseMiddleware(app)

        response = Response(content="test")

        header_value = middleware._get_response_header(response, "X-Non-Existent", "default")
        assert header_value == "default"

    def test_custom_middleware_integration(self) -> None:
        """Test custom middleware integration with FastAPI."""
        app = FastAPI()
        app.add_middleware(CustomMiddleware)

        @app.get("/")
        async def root():
            return {"message": "test"}

        client = TestClient(app)
        response = client.get("/")

        assert response.status_code == 200
        assert response.headers["X-Custom-Header"] == "test-value"

    def test_exception_handling_in_middleware(self) -> None:
        """Test exception handling in middleware."""
        app = FastAPI()
        app.add_middleware(BaseMiddleware)

        @app.get("/error")
        async def error_route():
            raise ValueError("Test error")

        client = TestClient(app)

        with pytest.raises(ValueError):
            client.get("/error")

    def test_middleware_order(self) -> None:
        """Test that middleware processes in correct order."""
        app = FastAPI()

        class FirstMiddleware(BaseMiddleware):
            async def _process_request(
                self,
                request: Request,
                call_next: RequestResponseEndpoint,
            ) -> Response:
                response = await super()._process_request(request, call_next)
                response.headers["X-First"] = "true"
                return response

        class SecondMiddleware(BaseMiddleware):
            async def _process_request(
                self,
                request: Request,
                call_next: RequestResponseEndpoint,
            ) -> Response:
                response = await super()._process_request(request, call_next)
                response.headers["X-Second"] = "true"
                return response

        app.add_middleware(SecondMiddleware)
        app.add_middleware(FirstMiddleware)

        @app.get("/")
        async def root():
            return {"message": "test"}

        client = TestClient(app)
        response = client.get("/")

        assert "X-First" in response.headers
        assert "X-Second" in response.headers


class TestMiddlewareLogging:
    """Test middleware logging functionality."""

    def test_log_requests(self, caplog: pytest.LogCaptureFixture) -> None:
        """Test request logging."""
        app = FastAPI()
        app.add_middleware(BaseMiddleware, log_requests=True)

        @app.get("/")
        async def root():
            return {"message": "test"}

        client = TestClient(app)

        with caplog.at_level("INFO"):
            response = client.get("/")

        assert response.status_code == 200
        assert any("Incoming request" in record.message for record in caplog.records)

    def test_log_responses(self, caplog: pytest.LogCaptureFixture) -> None:
        """Test response logging."""
        app = FastAPI()
        app.add_middleware(BaseMiddleware, log_responses=True)

        @app.get("/")
        async def root():
            return {"message": "test"}

        client = TestClient(app)

        with caplog.at_level("INFO"):
            response = client.get("/")

        assert response.status_code == 200
        assert any("Outgoing response" in record.message for record in caplog.records)

    def test_log_with_request_id(self, caplog: pytest.LogCaptureFixture) -> None:
        """Test logging with request ID."""
        app = FastAPI()
        app.add_middleware(BaseMiddleware, log_requests=True)

        @app.get("/")
        async def root():
            return {"message": "test"}

        client = TestClient(app)

        with caplog.at_level("INFO"):
            response = client.get("/")

        assert response.status_code == 200
