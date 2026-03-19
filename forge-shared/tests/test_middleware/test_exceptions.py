"""Tests for ExceptionHandlerMiddleware."""

from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field, ValidationError

from forge_shared.middleware.exceptions import ExceptionHandlerMiddleware


class TestRequestModel(BaseModel):
    """Test model for validation."""

    name: str = Field(..., min_length=1)
    age: int = Field(..., gt=0)


class TestExceptionHandlerMiddleware:
    """Test ExceptionHandlerMiddleware functionality."""

    def test_validation_error_handling(self) -> None:
        """Test Pydantic validation error handling."""
        from pydantic import BaseModel, Field

        class TestModel(BaseModel):
            name: str = Field(..., min_length=1)
            age: int = Field(..., gt=0)

        app = FastAPI()
        app.add_middleware(ExceptionHandlerMiddleware)

        @app.post("/")
        async def root(data: TestModel):
            return {"message": "ok"}

        client = TestClient(app)
        # This will trigger FastAPI's built-in validation
        response = client.post("/", json={"name": "", "age": -1})

        # FastAPI handles validation before our middleware
        # So we get the standard FastAPI validation error response
        assert response.status_code == 422
        data = response.json()
        assert "detail" in data

    def test_value_error_handling(self) -> None:
        """Test ValueError handling."""
        app = FastAPI()
        app.add_middleware(ExceptionHandlerMiddleware)

        @app.get("/")
        async def root():
            raise ValueError("Invalid value")

        client = TestClient(app)
        response = client.get("/")

        assert response.status_code == 400
        data = response.json()
        assert "error" in data
        assert data["error"] == "Bad Request"
        assert "Invalid value" in data["message"]

    def test_unexpected_error_handling(self) -> None:
        """Test unexpected error handling."""
        app = FastAPI()
        app.add_middleware(ExceptionHandlerMiddleware)

        @app.get("/")
        async def root():
            raise RuntimeError("Unexpected error")

        client = TestClient(app)
        response = client.get("/")

        assert response.status_code == 500
        data = response.json()
        assert "error" in data
        assert data["error"] == "Internal Server Error"

    def test_debug_mode(self) -> None:
        """Test debug mode includes debug information."""
        app = FastAPI()
        app.add_middleware(ExceptionHandlerMiddleware, debug=True)

        @app.get("/")
        async def root():
            raise RuntimeError("Test error")

        client = TestClient(app)
        response = client.get("/")

        assert response.status_code == 500
        data = response.json()
        assert "debug" in data
        assert data["debug"]["type"] == "RuntimeError"
        assert data["debug"]["message"] == "Test error"

    def test_no_debug_mode(self) -> None:
        """Test that debug is not included in production mode."""
        app = FastAPI()
        app.add_middleware(ExceptionHandlerMiddleware, debug=False)

        @app.get("/")
        async def root():
            raise RuntimeError("Test error")

        client = TestClient(app)
        response = client.get("/")

        assert response.status_code == 500
        data = response.json()
        assert "debug" not in data

    def test_request_id_included(self) -> None:
        """Test that request ID is included in error response."""
        from forge_shared.middleware.request_id import RequestIDMiddleware

        app = FastAPI()
        app.add_middleware(RequestIDMiddleware)
        app.add_middleware(ExceptionHandlerMiddleware)

        @app.get("/")
        async def root():
            raise ValueError("Test error")

        client = TestClient(app)
        response = client.get("/", headers={"X-Request-ID": "test-request-id"})

        assert response.status_code == 400
        data = response.json()
        assert "request_id" in data
        assert data["request_id"] == "test-request-id"

    def test_http_exception_not_handled(self) -> None:
        """Test that HTTPException is not handled by middleware."""
        app = FastAPI()
        app.add_middleware(ExceptionHandlerMiddleware)

        @app.get("/")
        async def root():
            raise HTTPException(status_code=404, detail="Not found")

        client = TestClient(app)
        response = client.get("/")

        # HTTPException should pass through
        assert response.status_code == 404

    def test_successful_requests_pass_through(self) -> None:
        """Test that successful requests pass through."""
        app = FastAPI()
        app.add_middleware(ExceptionHandlerMiddleware)

        @app.get("/")
        async def root():
            return {"message": "success"}

        client = TestClient(app)
        response = client.get("/")

        assert response.status_code == 200
        assert response.json() == {"message": "success"}

    def test_log_errors_disabled(self, caplog: pytest.LogCaptureFixture) -> None:
        """Test error logging can be disabled."""
        app = FastAPI()
        app.add_middleware(ExceptionHandlerMiddleware, log_errors=False)

        @app.get("/")
        async def root():
            raise ValueError("Test error")

        client = TestClient(app)

        with caplog.at_level("ERROR"):
            response = client.get("/")

        assert response.status_code == 400
        assert not any("Value error" in record.message for record in caplog.records)

    def test_multiple_validation_errors(self) -> None:
        """Test handling multiple validation errors."""
        from pydantic import BaseModel, Field

        class TestModel(BaseModel):
            name: str = Field(..., min_length=1)
            age: int = Field(..., gt=0)

        app = FastAPI()
        app.add_middleware(ExceptionHandlerMiddleware)

        @app.post("/")
        async def root(data: TestModel):
            return {"message": "ok"}

        client = TestClient(app)
        # This will trigger multiple validation errors
        response = client.post("/", json={"name": "", "age": -1})

        # FastAPI handles validation before our middleware
        assert response.status_code == 422
        data = response.json()
        assert "detail" in data
        assert isinstance(data["detail"], list)
        assert len(data["detail"]) >= 2


class TestMiddlewareIntegration:
    """Integration tests with other middleware."""

    def test_with_request_id_middleware(self) -> None:
        """Test exception handler works with request ID middleware."""
        from forge_shared.middleware.request_id import RequestIDMiddleware

        app = FastAPI()
        app.add_middleware(ExceptionHandlerMiddleware)
        app.add_middleware(RequestIDMiddleware)

        @app.get("/")
        async def root():
            raise ValueError("Test error")

        client = TestClient(app)
        response = client.get("/")

        assert response.status_code == 400
        data = response.json()
        # Check that request_id is in error response
        assert "request_id" in data
        # Check that request ID header is set (RequestIDMiddleware runs after ExceptionHandlerMiddleware)
        assert "X-Request-ID" in response.headers
