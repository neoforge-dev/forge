"""Tests for RequestIDMiddleware."""

import uuid

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from forge_shared.middleware.request_id import RequestIDMiddleware


class TestRequestIDMiddleware:
    """Test RequestIDMiddleware functionality."""

    def test_generates_request_id(self) -> None:
        """Test that middleware generates request ID."""
        app = FastAPI()
        app.add_middleware(RequestIDMiddleware)

        @app.get("/")
        async def root():
            return {"message": "test"}

        client = TestClient(app)
        response = client.get("/")

        assert "X-Request-ID" in response.headers
        assert len(response.headers["X-Request-ID"]) > 0

    def test_uses_existing_request_id(self) -> None:
        """Test that middleware uses existing request ID from header."""
        app = FastAPI()
        app.add_middleware(RequestIDMiddleware)

        @app.get("/")
        async def root():
            return {"message": "test"}

        client = TestClient(app)
        custom_id = "custom-request-id-123"
        response = client.get("/", headers={"X-Request-ID": custom_id})

        assert response.headers["X-Request-ID"] == custom_id

    def test_stores_request_id_in_state(self) -> None:
        """Test that middleware stores request ID in request state."""
        app = FastAPI()
        app.add_middleware(RequestIDMiddleware)

        request_id_captured = None

        @app.get("/")
        async def root(request: Request):
            nonlocal request_id_captured
            request_id_captured = getattr(request.state, "request_id", None)
            return {"message": "test"}

        client = TestClient(app)
        custom_id = "test-request-id"
        response = client.get("/", headers={"X-Request-ID": custom_id})

        assert request_id_captured == custom_id
        assert response.headers["X-Request-ID"] == custom_id

    def test_custom_header_name(self) -> None:
        """Test custom header name."""
        app = FastAPI()
        app.add_middleware(RequestIDMiddleware, header_name="X-Custom-Request-ID")

        @app.get("/")
        async def root():
            return {"message": "test"}

        client = TestClient(app)
        response = client.get("/")

        assert "X-Custom-Request-ID" in response.headers

    def test_custom_generator(self) -> None:
        """Test custom ID generator."""
        custom_id = "custom-generated-id"

        def custom_generator():
            return custom_id

        app = FastAPI()
        app.add_middleware(RequestIDMiddleware, generator=custom_generator)

        @app.get("/")
        async def root():
            return {"message": "test"}

        client = TestClient(app)
        response = client.get("/")

        assert response.headers["X-Request-ID"] == custom_id

    def test_validate_uuid_valid(self) -> None:
        """Test UUID validation with valid UUID."""
        valid_uuid = str(uuid.uuid4())

        app = FastAPI()
        app.add_middleware(RequestIDMiddleware, validate_uuid=True)

        @app.get("/")
        async def root():
            return {"message": "test"}

        client = TestClient(app)
        response = client.get("/", headers={"X-Request-ID": valid_uuid})

        assert response.headers["X-Request-ID"] == valid_uuid

    def test_validate_uuid_invalid(self) -> None:
        """Test UUID validation with invalid UUID."""
        app = FastAPI()
        app.add_middleware(RequestIDMiddleware, validate_uuid=True)

        @app.get("/")
        async def root():
            return {"message": "test"}

        client = TestClient(app)
        response = client.get("/", headers={"X-Request-ID": "not-a-uuid"})

        # Should generate new UUID
        assert response.headers["X-Request-ID"] != "not-a-uuid"
        # Should be valid UUID
        uuid.UUID(response.headers["X-Request-ID"])

    def test_different_request_ids(self) -> None:
        """Test that different requests get different IDs."""
        app = FastAPI()
        app.add_middleware(RequestIDMiddleware)

        @app.get("/")
        async def root():
            return {"message": "test"}

        client = TestClient(app)

        response1 = client.get("/")
        response2 = client.get("/")

        assert response1.headers["X-Request-ID"] != response2.headers["X-Request-ID"]

    def test_request_id_in_error_handler(self) -> None:
        """Test that request ID is available in error handlers."""
        app = FastAPI()
        app.add_middleware(RequestIDMiddleware)

        @app.get("/error")
        async def error_route():
            raise ValueError("Test error")

        client = TestClient(app)

        with pytest.raises(ValueError):
            client.get("/error", headers={"X-Request-ID": "test-id"})
