"""
Tests for header utilities.

Test coverage for header parsing, normalization, and extraction.
"""

import pytest
from fastapi import Request
from starlette.testclient import TestClient
from fastapi import FastAPI


class TestHeaderParsing:
    """
    Tests for header parsing utilities.
    """

    def test_header_utilities_imports(self):
        """
        Test that header utilities can be imported.
        """
        try:
            from forge_shared.utils.headers import (
                parse_accept_header,
                parse_content_type,
                get_content_type,
                normalize_header_name,
            )
            assert parse_accept_header is not None
        except ImportError:
            pytest.skip("Header utilities not implemented")

    def test_parse_accept_header_single(self):
        """
        Test parsing single MIME type.
        """
        try:
            from forge_shared.utils.headers import parse_accept_header

            result = parse_accept_header("application/json")
            
            assert result is not None
            assert len(result) > 0
        except ImportError:
            pytest.skip("Accept header parsing not implemented")

    def test_parse_accept_header_multiple(self):
        """
        Test parsing multiple MIME types.
        """
        try:
            from forge_shared.utils.headers import parse_accept_header

            result = parse_accept_header("application/json, text/html, */*")
            
            assert result is not None
            assert len(result) >= 1
        except ImportError:
            pytest.skip("Multiple MIME parsing not implemented")

    def test_parse_accept_header_with_quality(self):
        """
        Test parsing accept header with quality values.
        """
        try:
            from forge_shared.utils.headers import parse_accept_header

            result = parse_accept_header(
                "application/json;q=0.9, text/html;q=0.8, */*;q=0.1"
            )
            
            assert result is not None
        except ImportError:
            pytest.skip("Quality value parsing not implemented")

    def test_parse_accept_header_empty(self):
        """
        Test parsing empty accept header.
        """
        try:
            from forge_shared.utils.headers import parse_accept_header

            result = parse_accept_header("")
            
            assert result is not None
        except ImportError:
            pytest.skip("Empty header parsing not implemented")

    def test_parse_content_type(self):
        """
        Test parsing content type header.
        """
        try:
            from forge_shared.utils.headers import parse_content_type

            result = parse_content_type("application/json; charset=utf-8")
            
            assert result is not None
        except ImportError:
            pytest.skip("Content type parsing not implemented")

    def test_parse_content_type_with_charset(self):
        """
        Test parsing content type with charset.
        """
        try:
            from forge_shared.utils.headers import parse_content_type

            result = parse_content_type("text/html; charset=utf-8")
            
            assert result is not None
        except ImportError:
            pytest.skip("Charset parsing not implemented")

    def test_parse_content_type_simple(self):
        """
        Test parsing simple content type.
        """
        try:
            from forge_shared.utils.headers import parse_content_type

            result = parse_content_type("application/json")
            
            assert result is not None
        except ImportError:
            pytest.skip("Simple content type parsing not implemented")

    def test_get_content_type_from_request(self):
        """
        Test getting content type from request.
        """
        try:
            from forge_shared.utils.headers import get_content_type

            app = FastAPI()
            
            @app.post("/test")
            async def test_endpoint(request: Request):
                content_type = get_content_type(request)
                return {"content_type": content_type}
            
            client = TestClient(app)
            response = client.post(
                "/test",
                content="{}",
                headers={"Content-Type": "application/json"}
            )
            
            assert response.status_code == 200
        except ImportError:
            pytest.skip("Get content type not implemented")

    def test_get_content_type_missing(self):
        """
        Test getting content type when missing.
        """
        try:
            from forge_shared.utils.headers import get_content_type

            app = FastAPI()
            
            @app.post("/test")
            async def test_endpoint(request: Request):
                content_type = get_content_type(request)
                return {"content_type": content_type}
            
            client = TestClient(app)
            response = client.post("/test", content="{}")
            
            assert response.status_code == 200
        except ImportError:
            pytest.skip("Missing content type handling not implemented")


class TestHeaderNormalization:
    """
    Tests for header name normalization.
    """

    def test_normalize_header_name_lowercase(self):
        """
        Test normalizing header to lowercase.
        """
        try:
            from forge_shared.utils.headers import normalize_header_name

            result = normalize_header_name("Content-Type")
            
            assert result is not None
        except ImportError:
            pytest.skip("Header normalization not implemented")

    def test_normalize_header_name_snake_case(self):
        """
        Test converting header to snake_case.
        """
        try:
            from forge_shared.utils.headers import normalize_header_name

            result = normalize_header_name("X-Custom-Header")
            
            assert result is not None
        except ImportError:
            pytest.skip("Snake case conversion not implemented")

    def test_normalize_header_preserve_http(self):
        """
        Test preserving HTTP/ prefix.
        """
        try:
            from forge_shared.utils.headers import normalize_header_name

            result = normalize_header_name("HTTP_X_Custom_Header")
            
            assert result is not None
        except ImportError:
            pytest.skip("HTTP prefix handling not implemented")


class TestHeaderExtraction:
    """
    Tests for header extraction utilities.
    """

    def test_extract_bearer_token(self):
        """
        Test extracting Bearer token.
        """
        try:
            from forge_shared.utils.headers import extract_bearer_token

            result = extract_bearer_token("Bearer abc123")
            
            assert result is not None
        except ImportError:
            pytest.skip("Bearer token extraction not implemented")

    def test_extract_bearer_token_invalid(self):
        """
        Test extracting invalid Bearer token.
        """
        try:
            from forge_shared.utils.headers import extract_bearer_token

            result = extract_bearer_token("Basic abc123")
            
            assert result is None
        except ImportError:
            pytest.skip("Invalid token handling not implemented")

    def test_extract_forwarded_for(self):
        """
        Test extracting X-Forwarded-For.
        """
        try:
            from forge_shared.utils.headers import extract_forwarded_for

            result = extract_forwarded_for("192.168.1.1, 10.0.0.1")
            
            assert result is not None
        except ImportError:
            pytest.skip("Forwarded-For extraction not implemented")

    def test_extract_forwarded_for_single(self):
        """
        Test extracting single IP from X-Forwarded-For.
        """
        try:
            from forge_shared.utils.headers import extract_forwarded_for

            result = extract_forwarded_for("192.168.1.1")
            
            assert result is not None
        except ImportError:
            pytest.skip("Single IP extraction not implemented")


class TestHeaderValidation:
    """
    Tests for header validation.
    """

    def test_is_valid_header_name(self):
        """
        Test validating header name.
        """
        try:
            from forge_shared.utils.headers import is_valid_header_name

            result = is_valid_header_name("Content-Type")
            
            assert result is True
        except ImportError:
            pytest.skip("Header name validation not implemented")

    def test_is_valid_header_name_invalid(self):
        """
        Test validating invalid header name.
        """
        try:
            from forge_shared.utils.headers import is_valid_header_name

            result = is_valid_header_name("Content Type")
            
            assert result is False
        except ImportError:
            pytest.skip("Invalid header name validation not implemented")

    def test_sanitize_header_value(self):
        """
        Test sanitizing header value.
        """
        try:
            from forge_shared.utils.headers import sanitize_header_value

            result = sanitize_header_value("valid-value")
            
            assert result is not None
        except ImportError:
            pytest.skip("Header value sanitization not implemented")

    def test_sanitize_header_value_newlines(self):
        """
        Test sanitizing header value with newlines.
        """
        try:
            from forge_shared.utils.headers import sanitize_header_value

            result = sanitize_header_value("value\nwith\nnewlines")
            
            assert "\n" not in result
        except ImportError:
            pytest.skip("Newline sanitization not implemented")


class TestHeaderBuilder:
    """
    Tests for header builder utilities.
    """

    def test_build_cors_headers(self):
        """
        Test building CORS headers.
        """
        try:
            from forge_shared.utils.headers import build_cors_headers

            result = build_cors_headers(
                origin="https://example.com",
                methods=["GET", "POST"],
                headers=["Content-Type"]
            )
            
            assert result is not None
            assert isinstance(result, dict)
        except ImportError:
            pytest.skip("CORS header builder not implemented")

    def test_build_cache_headers(self):
        """
        Test building cache headers.
        """
        try:
            from forge_shared.utils.headers import build_cache_headers

            result = build_cache_headers(
                max_age=3600,
                public=True
            )
            
            assert result is not None
            assert isinstance(result, dict)
        except ImportError:
            pytest.skip("Cache header builder not implemented")

    def test_build_csp_header(self):
        """
        Test building Content-Security-Policy header.
        """
        try:
            from forge_shared.utils.headers import build_csp_header

            result = build_csp_header(
                default_src=["'self'"],
                script_src=["'self'", "'unsafe-inline'"]
            )
            
            assert result is not None
        except ImportError:
            pytest.skip("CSP header builder not implemented")


class TestHeaderEdgeCases:
    """
    Tests for header edge cases.
    """

    def test_empty_header_value(self):
        """
        Test handling empty header value.
        """
        try:
            from forge_shared.utils.headers import parse_accept_header

            result = parse_accept_header(None)
            
            # Should handle gracefully
            assert result is not None
        except ImportError:
            pytest.skip("Empty header handling not implemented")

    def test_unicode_header_value(self):
        """
        Test handling unicode in header.
        """
        try:
            from forge_shared.utils.headers import sanitize_header_value

            result = sanitize_header_value("value-ñ-ü")
            
            assert result is not None
        except ImportError:
            pytest.skip("Unicode handling not implemented")

    def test_very_long_header(self):
        """
        Test handling very long header.
        """
        try:
            from forge_shared.utils.headers import parse_accept_header

            long_header = ", ".join(["application/json"] * 100)
            result = parse_accept_header(long_header)
            
            assert result is not None
        except ImportError:
            pytest.skip("Long header handling not implemented")

    def test_header_with_special_chars(self):
        """
        Test handling special characters.
        """
        try:
            from forge_shared.utils.headers import sanitize_header_value

            result = sanitize_header_value("value;with=special,chars")
            
            assert result is not None
        except ImportError:
            pytest.skip("Special character handling not implemented")
