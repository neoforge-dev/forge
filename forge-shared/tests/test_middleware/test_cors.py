"""Tests for CORSMiddleware."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from forge_shared.middleware.cors import CORSMiddleware


class TestCORSMiddleware:
    """Test CORSMiddleware functionality."""

    def test_cors_headers_added(self) -> None:
        """Test that CORS headers are added."""
        app = FastAPI()
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["https://example.com"],
        )

        @app.get("/")
        async def root():
            return {"message": "test"}

        client = TestClient(app)
        response = client.get("/", headers={"origin": "https://example.com"})

        assert "access-control-allow-origin" in response.headers
        assert response.headers["access-control-allow-origin"] == "https://example.com"

    def test_preflight_request(self) -> None:
        """Test OPTIONS preflight request handling."""
        app = FastAPI()
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["https://example.com"],
            allow_methods=["GET", "POST"],
        )

        @app.get("/")
        async def root():
            return {"message": "test"}

        client = TestClient(app)
        response = client.options("/", headers={"origin": "https://example.com"})

        assert response.status_code == 200
        assert "access-control-allow-methods" in response.headers
        assert "access-control-max-age" in response.headers

    def test_wildcard_origin(self) -> None:
        """Test wildcard origin."""
        app = FastAPI()
        app.add_middleware(
            CORSMiddleware,
            allow_origins="*",
        )

        @app.get("/")
        async def root():
            return {"message": "test"}

        client = TestClient(app)
        response = client.get("/", headers={"origin": "https://any-origin.com"})

        assert "access-control-allow-origin" in response.headers

    def test_multiple_origins(self) -> None:
        """Test multiple allowed origins."""
        app = FastAPI()
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["https://example1.com", "https://example2.com"],
        )

        @app.get("/")
        async def root():
            return {"message": "test"}

        client = TestClient(app)

        response1 = client.get("/", headers={"origin": "https://example1.com"})
        assert response1.headers["access-control-allow-origin"] == "https://example1.com"

        response2 = client.get("/", headers={"origin": "https://example2.com"})
        assert response2.headers["access-control-allow-origin"] == "https://example2.com"

    def test_disallowed_origin(self) -> None:
        """Test that disallowed origin doesn't get CORS headers."""
        app = FastAPI()
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["https://example.com"],
        )

        @app.get("/")
        async def root():
            return {"message": "test"}

        client = TestClient(app)
        response = client.get("/", headers={"origin": "https://malicious.com"})

        assert "access-control-allow-origin" not in response.headers

    def test_wildcard_subdomain(self) -> None:
        """Test wildcard subdomain matching."""
        app = FastAPI()
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*.example.com"],
        )

        @app.get("/")
        async def root():
            return {"message": "test"}

        client = TestClient(app)

        response1 = client.get("/", headers={"origin": "https://api.example.com"})
        assert "access-control-allow-origin" in response1.headers

        response2 = client.get("/", headers={"origin": "https://app.example.com"})
        assert "access-control-allow-origin" in response2.headers

        response3 = client.get("/", headers={"origin": "https://other.com"})
        assert "access-control-allow-origin" not in response3.headers

    def test_allow_credentials(self) -> None:
        """Test allow credentials."""
        app = FastAPI()
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["https://example.com"],
            allow_credentials=True,
        )

        @app.get("/")
        async def root():
            return {"message": "test"}

        client = TestClient(app)
        response = client.get("/", headers={"origin": "https://example.com"})

        assert response.headers.get("access-control-allow-credentials") == "true"

    def test_expose_headers(self) -> None:
        """Test expose headers."""
        app = FastAPI()
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["https://example.com"],
            expose_headers=["X-Custom-Header", "X-Another-Header"],
        )

        @app.get("/")
        async def root():
            return {"message": "test"}

        client = TestClient(app)
        response = client.get("/", headers={"origin": "https://example.com"})

        assert "access-control-expose-headers" in response.headers
        exposed = response.headers["access-control-expose-headers"]
        assert "X-Custom-Header" in exposed
        assert "X-Another-Header" in exposed

    def test_max_age(self) -> None:
        """Test max-age for preflight cache."""
        app = FastAPI()
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["https://example.com"],
            max_age=3600,
        )

        @app.get("/")
        async def root():
            return {"message": "test"}

        client = TestClient(app)
        response = client.options("/", headers={"origin": "https://example.com"})

        assert response.headers["access-control-max-age"] == "3600"

    def test_custom_methods(self) -> None:
        """Test custom allowed methods."""
        app = FastAPI()
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["https://example.com"],
            allow_methods=["GET", "POST", "PUT"],
        )

        @app.get("/")
        async def root():
            return {"message": "test"}

        client = TestClient(app)
        response = client.options("/", headers={"origin": "https://example.com"})

        methods = response.headers["access-control-allow-methods"]
        assert "GET" in methods
        assert "POST" in methods
        assert "PUT" in methods

    def test_custom_headers(self) -> None:
        """Test custom allowed headers."""
        app = FastAPI()
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["https://example.com"],
            allow_headers=["X-Custom-Header", "Content-Type"],
        )

        @app.get("/")
        async def root():
            return {"message": "test"}

        client = TestClient(app)
        response = client.options(
            "/",
            headers={
                "origin": "https://example.com",
                "access-control-request-headers": "X-Custom-Header",
            },
        )

        assert "access-control-allow-headers" in response.headers

    def test_no_origin_header(self) -> None:
        """Test request without origin header."""
        app = FastAPI()
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["https://example.com"],
        )

        @app.get("/")
        async def root():
            return {"message": "test"}

        client = TestClient(app)
        response = client.get("/")

        assert "access-control-allow-origin" not in response.headers
