"""Tests for configuration validators."""

import pytest

from forge_shared.config.validators import (
    validate_url,
    validate_email,
    validate_port,
    validate_positive_int,
    validate_non_empty_string,
    validate_comma_separated_list,
    validate_log_level,
    validate_environment,
)


class TestValidateUrl:
    """Test URL validator."""

    def test_valid_urls(self) -> None:
        """Test valid URLs."""
        valid_urls = [
            "http://example.com",
            "https://example.com",
            "https://example.com:8080",
            "https://example.com/path",
            "https://example.com/path?query=value",
            "https://user:pass@example.com",
            "wss://example.com/socket",
        ]

        for url in valid_urls:
            assert validate_url(url) == url

    def test_invalid_urls(self) -> None:
        """Test invalid URLs raise errors."""
        invalid_urls = [
            "not-a-url",
            "example.com",
            "",
            "   ",
        ]

        for url in invalid_urls:
            with pytest.raises(ValueError):
                validate_url(url)

    def test_none_returns_none(self) -> None:
        """Test None returns None."""
        assert validate_url(None) is None

    def test_whitespace_trimming(self) -> None:
        """Test whitespace is trimmed."""
        assert validate_url("  https://example.com  ") == "https://example.com"


class TestValidateEmail:
    """Test email validator."""

    def test_valid_emails(self) -> None:
        """Test valid email addresses."""
        valid_emails = [
            "user@example.com",
            "user.name@example.com",
            "user+tag@example.com",
            "USER@EXAMPLE.COM",
        ]

        for email in valid_emails:
            result = validate_email(email)
            assert result == email.lower()

    def test_invalid_emails(self) -> None:
        """Test invalid email addresses raise errors."""
        invalid_emails = [
            "@example.com",
            "user@",
            "",
            "   ",
        ]

        for email in invalid_emails:
            with pytest.raises(ValueError):
                validate_email(email)

    def test_none_returns_none(self) -> None:
        """Test None returns None."""
        assert validate_email(None) is None


class TestValidatePort:
    """Test port validator."""

    def test_valid_ports(self) -> None:
        """Test valid port numbers."""
        valid_ports = [1, 80, 443, 8080, 65535]

        for port in valid_ports:
            assert validate_port(port) == port

    def test_port_from_string(self) -> None:
        """Test port from string."""
        assert validate_port("8080") == 8080

    def test_invalid_ports(self) -> None:
        """Test invalid port numbers raise errors."""
        invalid_ports = [0, -1, 65536, 100000, "invalid"]

        for port in invalid_ports:
            with pytest.raises(ValueError):
                validate_port(port)


class TestValidatePositiveInt:
    """Test positive integer validator."""

    def test_valid_values(self) -> None:
        """Test valid positive integers."""
        valid_values = [0, 1, 100, 1000]

        for value in valid_values:
            assert validate_positive_int(value) == value

    def test_from_string(self) -> None:
        """Test conversion from string."""
        assert validate_positive_int("42") == 42

    def test_negative_values(self) -> None:
        """Test negative values raise errors."""
        with pytest.raises(ValueError):
            validate_positive_int(-1)

    def test_invalid_types(self) -> None:
        """Test invalid types raise errors."""
        with pytest.raises(ValueError):
            validate_positive_int("invalid")


class TestValidateNonEmptyString:
    """Test non-empty string validator."""

    def test_valid_strings(self) -> None:
        """Test valid strings."""
        valid_strings = ["test", "hello world", "  test  "]

        for string in valid_strings:
            result = validate_non_empty_string(string)
            assert result.strip() == string.strip()

    def test_empty_string(self) -> None:
        """Test empty string raises error."""
        with pytest.raises(ValueError):
            validate_non_empty_string("")

    def test_whitespace_only(self) -> None:
        """Test whitespace-only string raises error."""
        with pytest.raises(ValueError):
            validate_non_empty_string("   ")

    def test_non_string_types(self) -> None:
        """Test non-string types raise errors."""
        with pytest.raises(ValueError):
            validate_non_empty_string(123)


class TestValidateCommaSeparatedList:
    """Test comma-separated list validator."""

    def test_from_string(self) -> None:
        """Test parsing from string."""
        result = validate_comma_separated_list("item1,item2,item3")
        assert result == ["item1", "item2", "item3"]

    def test_from_list(self) -> None:
        """Test from list."""
        result = validate_comma_separated_list(["item1", "item2", "item3"])
        assert result == ["item1", "item2", "item3"]

    def test_whitespace_handling(self) -> None:
        """Test whitespace is handled."""
        result = validate_comma_separated_list("item1, item2 , item3")
        assert result == ["item1", "item2", "item3"]

    def test_empty_items_filtered(self) -> None:
        """Test empty items are filtered out."""
        result = validate_comma_separated_list("item1,,item2")
        assert result == ["item1", "item2"]

    def test_invalid_type(self) -> None:
        """Test invalid type raises error."""
        with pytest.raises(ValueError):
            validate_comma_separated_list(123)


class TestValidateLogLevel:
    """Test log level validator."""

    def test_valid_log_levels(self) -> None:
        """Test valid log levels."""
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

        for level in valid_levels:
            result = validate_log_level(level)
            assert result == level

    def test_case_insensitive(self) -> None:
        """Test case insensitivity."""
        assert validate_log_level("debug") == "DEBUG"
        assert validate_log_level("Info") == "INFO"

    def test_whitespace_trimming(self) -> None:
        """Test whitespace trimming."""
        assert validate_log_level("  debug  ") == "DEBUG"

    def test_invalid_log_levels(self) -> None:
        """Test invalid log levels raise errors."""
        invalid_levels = ["TRACE", "VERBOSE", "INVALID"]

        for level in invalid_levels:
            with pytest.raises(ValueError):
                validate_log_level(level)

    def test_non_string_types(self) -> None:
        """Test non-string types raise errors."""
        with pytest.raises(ValueError):
            validate_log_level(123)


class TestValidateEnvironment:
    """Test environment validator."""

    def test_valid_environments(self) -> None:
        """Test valid environments."""
        valid_environments = ["development", "staging", "production", "testing"]

        for env in valid_environments:
            result = validate_environment(env)
            assert result == env

    def test_case_insensitive(self) -> None:
        """Test case insensitivity."""
        assert validate_environment("DEVELOPMENT") == "development"
        assert validate_environment("Production") == "production"

    def test_whitespace_trimming(self) -> None:
        """Test whitespace trimming."""
        assert validate_environment("  production  ") == "production"

    def test_invalid_environments(self) -> None:
        """Test invalid environments raise errors."""
        invalid_environments = ["dev", "prod", "uat", "invalid"]

        for env in invalid_environments:
            with pytest.raises(ValueError):
                validate_environment(env)

    def test_non_string_types(self) -> None:
        """Test non-string types raise errors."""
        with pytest.raises(ValueError):
            validate_environment(123)
