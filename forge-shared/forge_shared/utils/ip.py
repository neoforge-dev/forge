"""
IP address utility functions.

Provides functions for IP address validation, extraction, and classification.

Example:
    ```python
    from forge_shared.utils.ip import get_client_ip, is_private_ip

    ip = get_client_ip(request)
    if is_private_ip(ip):
        print("Internal request")
    ```
"""

from typing import Optional
from ipaddress import IPv4Address, IPv6Address, ip_address, AddressValueError
from fastapi import Request


def get_client_ip(request: Request) -> Optional[str]:
    """
    Extract client IP address from request.

    Checks multiple headers for IP address, including X-Forwarded-For,
    X-Real-IP, and falls back to direct connection IP.

    Args:
        request: FastAPI request object

    Returns:
        Client IP address or None

    Example:
        ```python
        ip = get_client_ip(request)
        print(f"Client IP: {ip}")
        ```
    """
    # Check X-Forwarded-For header
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        # X-Forwarded-For can contain multiple IPs, use the first one
        return forwarded_for.split(",")[0].strip()

    # Check X-Real-IP header
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip

    # Fall back to direct connection IP
    if request.client:
        return request.client.host

    return None


def is_valid_ip(ip: str) -> bool:
    """
    Validate IP address format.

    Args:
        ip: IP address string

    Returns:
        True if IP is valid

    Example:
        ```python
        if is_valid_ip("192.168.1.1"):
            print("Valid IP")
        ```
    """
    try:
        ip_address(ip)
        return True
    except (ValueError, AddressValueError):
        return False


def is_private_ip(ip: str) -> bool:
    """
    Check if IP address is private/internal.

    Args:
        ip: IP address string

    Returns:
        True if IP is private

    Example:
        ```python
        if is_private_ip(ip):
            print("Internal request")
        ```
    """
    try:
        addr = ip_address(ip)
        return addr.is_private
    except (ValueError, AddressValueError):
        return False


def is_ipv4(ip: str) -> bool:
    """
    Check if IP address is IPv4.

    Args:
        ip: IP address string

    Returns:
        True if IP is IPv4
    """
    try:
        return isinstance(ip_address(ip), IPv4Address)
    except (ValueError, AddressValueError):
        return False


def is_ipv6(ip: str) -> bool:
    """
    Check if IP address is IPv6.

    Args:
        ip: IP address string

    Returns:
        True if IP is IPv6
    """
    try:
        return isinstance(ip_address(ip), IPv6Address)
    except (ValueError, AddressValueError):
        return False


def normalize_ip(ip: str) -> Optional[str]:
    """
    Normalize IP address to string representation.

    Args:
        ip: IP address string

    Returns:
        Normalized IP address or None if invalid

    Example:
        ```python
        normalized = normalize_ip("127.0.0.1")
        print(normalized)  # "127.0.0.1"
        ```
    """
    try:
        return str(ip_address(ip))
    except (ValueError, AddressValueError):
        return None
