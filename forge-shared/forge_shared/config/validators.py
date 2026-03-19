"""
Configuration validators for common use cases.

Provides reusable validators for configuration values.
"""

import re
from typing import Any, List, Optional
from pydantic import BeforeValidator


def validate_url(value: Any) -> Optional[str]:
    """
    Validate URL string.

    Args:
        value: URL to validate

    Returns:
        Validated URL string

    Raises:
        ValueError: If URL is invalid
    """
    if value is None:
        return None

    if not isinstance(value, str):
        raise ValueError(f"URL must be a string, got {type(value).__name__}")

    value = value.strip()

    if not value:
        raise ValueError("URL cannot be empty")

    # Basic URL validation
    url_pattern = re.compile(
        r"^(?:http|https|ws|wss)://"  # protocol
        r"(?:\S+@\S+)?"  # user:pass authentication
        r"(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}"  # domain
        r"(?::\d+)?"  # port
        r"(?:/\S*)?$",  # path
        re.IGNORECASE,
    )

    if not url_pattern.match(value):
        raise ValueError(f"Invalid URL format: {value}")

    return value


def validate_email(value: Any) -> Optional[str]:
    """
    Validate email address.

    Args:
        value: Email to validate

    Returns:
        Validated email string

    Raises:
        ValueError: If email is invalid
    """
    if value is None:
        return None

    if not isinstance(value, str):
        raise ValueError(f"Email must be a string, got {type(value).__name__}")

    value = value.strip()

    if not value:
        raise ValueError("Email cannot be empty")

    # Basic email validation
    email_pattern = re.compile(
        r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$",
        re.IGNORECASE,
    )

    if not email_pattern.match(value):
        raise ValueError(f"Invalid email format: {value}")

    return value.lower()


def validate_port(value: Any) -> int:
    """
    Validate port number.

    Args:
        value: Port to validate

    Returns:
        Validated port number

    Raises:
        ValueError: If port is invalid
    """
    if not isinstance(value, int):
        try:
            value = int(value)
        except (ValueError, TypeError):
            raise ValueError(f"Port must be an integer, got {type(value).__name__}")

    if not 1 <= value <= 65535:
        raise ValueError(f"Port must be between 1 and 65535, got {value}")

    return value


def validate_positive_int(value: Any) -> int:
    """
    Validate positive integer.

    Args:
        value: Value to validate

    Returns:
        Validated positive integer

    Raises:
        ValueError: If value is invalid
    """
    if not isinstance(value, int):
        try:
            value = int(value)
        except (ValueError, TypeError):
            raise ValueError(f"Value must be an integer, got {type(value).__name__}")

    if value < 0:
        raise ValueError(f"Value must be positive, got {value}")

    return value


def validate_non_empty_string(value: Any) -> str:
    """
    Validate non-empty string.

    Args:
        value: Value to validate

    Returns:
        Validated string

    Raises:
        ValueError: If value is invalid
    """
    if not isinstance(value, str):
        raise ValueError(f"Value must be a string, got {type(value).__name__}")

    value = value.strip()

    if not value:
        raise ValueError("String cannot be empty")

    return value


def validate_comma_separated_list(value: Any) -> List[str]:
    """
    Validate comma-separated list.

    Args:
        value: Value to validate

    Returns:
        List of strings

    Raises:
        ValueError: If value is invalid
    """
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]

    if isinstance(value, str):
        items = [item.strip() for item in value.split(",")]
        return [item for item in items if item]

    raise ValueError(f"Value must be a string or list, got {type(value).__name__}")


def validate_log_level(value: Any) -> str:
    """
    Validate log level.

    Args:
        value: Log level to validate

    Returns:
        Validated log level

    Raises:
        ValueError: If log level is invalid
    """
    if not isinstance(value, str):
        raise ValueError(f"Log level must be a string, got {type(value).__name__}")

    value = value.strip().upper()

    valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}

    if value not in valid_levels:
        raise ValueError(f"Log level must be one of {valid_levels}, got {value}")

    return value


def validate_environment(value: Any) -> str:
    """
    Validate environment name.

    Args:
        value: Environment to validate

    Returns:
        Validated environment name

    Raises:
        ValueError: If environment is invalid
    """
    if not isinstance(value, str):
        raise ValueError(f"Environment must be a string, got {type(value).__name__}")

    value = value.strip().lower()

    valid_environments = {"development", "staging", "production", "testing"}

    if value not in valid_environments:
        raise ValueError(f"Environment must be one of {valid_environments}, got {value}")

    return value


# Type aliases for common validators
ValidatedUrl = BeforeValidator(validate_url)
ValidatedEmail = BeforeValidator(validate_email)
ValidatedPort = BeforeValidator(validate_port)
ValidatedPositiveInt = BeforeValidator(validate_positive_int)
ValidatedNonEmptyString = BeforeValidator(validate_non_empty_string)
ValidatedLogLevel = BeforeValidator(validate_log_level)
ValidatedEnvironment = BeforeValidator(validate_environment)
