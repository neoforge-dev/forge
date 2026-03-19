"""
Tests for utility functions.

Test coverage for IP utilities, header utilities, and other helper functions.
"""

import pytest
from unittest.mock import MagicMock
from fastapi import Request


class TestIPUtilities:
    """
    Tests for IP address extraction and validation.

    Tests IP parsing, proxy headers, and trust chains.
    """

    def test_ip_utils_imports(self):
        """
        Test that IP utilities can be imported.
        """
        try:
            from forge_shared.utils.ip import get_client_ip, is_valid_ip
            assert get_client_ip is not None
        except ImportError:
            pytest.skip("IP utilities not implemented")

    def test_get_client_ip_from_socket(self):
        """
        Test getting client IP from socket.
        """
        try:
            from forge_shared.utils.ip import get_client_ip

            request = MagicMock(spec=Request)
            request.client = MagicMock()
            request.client.host = "192.168.1.1"
            request.headers = {}

            result = get_client_ip(request)
            assert result == "192.168.1.1"
        except ImportError:
            pytest.skip("IP utilities not implemented")

    def test_get_client_ip_from_x_forwarded_for(self):
        """
        Test getting client IP from X-Forwarded-For header.
        """
        try:
            from forge_shared.utils.ip import get_client_ip

            request = MagicMock(spec=Request)
            request.client = MagicMock()
            request.client.host = "127.0.0.1"
            request.headers = {"X-Forwarded-For": "203.0.113.1, 70.41.3.18"}

            result = get_client_ip(request)
            # Should return first IP in chain
            assert "203.0.113.1" in result
        except ImportError:
            pytest.skip("IP utilities not implemented")

    def test_get_client_ip_from_x_real_ip(self):
        """
        Test getting client IP from X-Real-IP header.
        """
        try:
            from forge_shared.utils.ip import get_client_ip

            request = MagicMock(spec=Request)
            request.client = MagicMock()
            request.client.host = "127.0.0.1"
            request.headers = {"X-Real-IP": "203.0.113.50"}

            result = get_client_ip(request)
            assert result == "203.0.113.50"
        except ImportError:
            pytest.skip("IP utilities not implemented")

    def test_is_valid_ip_ipv4(self):
        """
        Test IPv4 validation.
        """
        try:
            from forge_shared.utils.ip import is_valid_ip

            assert is_valid_ip("192.168.1.1") is True
            assert is_valid_ip("10.0.0.1") is True
            assert is_valid_ip("172.16.0.1") is True
            assert is_valid_ip("invalid") is False
            assert is_valid_ip("256.256.256.256") is False
        except ImportError:
            pytest.skip("IP utilities not implemented")

    def test_is_valid_ip_ipv6(self):
        """
        Test IPv6 validation.
        """
        try:
            from forge_shared.utils.ip import is_valid_ip

            assert is_valid_ip("::1") is True
            assert is_valid_ip("2001:0db8:85a3:0000:0000:8a2e:0370:7334") is True
            assert is_valid_ip("fe80::") is True
        except ImportError:
            pytest.skip("IP utilities not implemented")

    def test_parse_forwarded_chain(self):
        """
        Test parsing X-Forwarded-For chain.
        """
        try:
            from forge_shared.utils.ip import parse_forwarded_chain

            chain = "203.0.113.1, 70.41.3.18, 150.172.238.178"
            result = parse_forwarded_chain(chain)
            
            assert len(result) == 3
            assert result[0] == "203.0.113.1"
            assert result[1] == "70.41.3.18"
            assert result[2] == "150.172.238.178"
        except ImportError:
            pytest.skip("IP utilities not implemented")


class TestHeaderUtilities:
    """
    Tests for header manipulation and extraction.

    Tests header parsing, normalization, and security.
    """

    def test_header_utils_imports(self):
        """
        Test that header utilities can be imported.
        """
        try:
            from forge_shared.utils.headers import (
                normalize_header_name,
                parse_cache_control,
                get_content_type,
            )
            assert normalize_header_name is not None
        except ImportError:
            pytest.skip("Header utilities not implemented")

    def test_normalize_header_name(self):
        """
        Test header name normalization.
        """
        try:
            from forge_shared.utils.headers import normalize_header_name

            assert normalize_header_name("content-type") == "Content-Type"
            assert normalize_header_name("x-custom-header") == "X-Custom-Header"
            assert normalize_header_name("CONTENT-TYPE") == "Content-Type"
        except ImportError:
            pytest.skip("Header utilities not implemented")

    def test_parse_cache_control(self):
        """
        Test Cache-Control header parsing.
        """
        try:
            from forge_shared.utils.headers import parse_cache_control

            header = "max-age=3600, public, no-transform"
            result = parse_cache_control(header)
            
            assert result["max-age"] == 3600
            assert result["public"] is True
            assert result["no-transform"] is True
        except ImportError:
            pytest.skip("Header utilities not implemented")

    def test_get_content_type(self):
        """
        Test content type extraction.
        """
        try:
            from forge_shared.utils.headers import get_content_type

            request = MagicMock(spec=Request)
            # Use proper case for HTTP headers (or mock get method)
            request.headers.get = MagicMock(return_value="application/json; charset=utf-8")

            result = get_content_type(request)
            assert result == "application/json"
        except ImportError:
            pytest.skip("Header utilities not implemented")

    def test_extract_rate_limit_headers(self):
        """
        Test rate limit header extraction.
        """
        try:
            from forge_shared.utils.headers import extract_rate_limit_headers

            headers = {
                "X-RateLimit-Limit": "100",
                "X-RateLimit-Remaining": "95",
                "X-RateLimit-Reset": "1234567890"
            }

            result = extract_rate_limit_headers(headers)
            assert result["limit"] == 100
            assert result["remaining"] == 95
            assert result["reset"] == 1234567890
        except ImportError:
            pytest.skip("Header utilities not implemented")

    def test_sanitize_headers(self):
        """
        Test header sanitization for logging.
        """
        try:
            from forge_shared.utils.headers import sanitize_headers

            headers = {
                "authorization": "Bearer secret-token",
                "x-api-key": "secret-key",
                "content-type": "application/json"
            }

            result = sanitize_headers(headers)
            
            # Sensitive headers should be redacted
            assert result["authorization"] != "Bearer secret-token"
            assert result["x-api-key"] != "secret-key"
            assert result["content-type"] == "application/json"
        except ImportError:
            pytest.skip("Header utilities not implemented")


class TestProxyDetection:
    """
    Tests for proxy detection and trust chain.

    Tests trusted proxy configuration and validation.
    """

    def test_is_trusted_proxy(self):
        """
        Test trusted proxy detection.
        """
        try:
            from forge_shared.utils.ip import is_trusted_proxy

            # Common internal IPs
            assert is_trusted_proxy("127.0.0.1") is True
            assert is_trusted_proxy("10.0.0.1") is True
            assert is_trusted_proxy("172.16.0.1") is True
            assert is_trusted_proxy("192.168.1.1") is True
            
            # Public IPs should not be trusted
            assert is_trusted_proxy("8.8.8.8") is False
        except ImportError:
            pytest.skip("Proxy detection not implemented")

    def test_get_first_untrusted_ip(self):
        """
        Test getting first untrusted IP from chain.
        """
        try:
            from forge_shared.utils.ip import get_first_untrusted_ip

            chain = ["127.0.0.1", "10.0.0.1", "203.0.113.1", "192.168.1.1"]
            result = get_first_untrusted_ip(chain)
            
            # Should return first public IP
            assert result == "203.0.113.1"
        except ImportError:
            pytest.skip("IP chain parsing not implemented")
