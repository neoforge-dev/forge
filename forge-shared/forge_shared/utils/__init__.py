"""
Utility functions for common operations.

Provides helper functions for IP address handling, HTTP header parsing,
and other common utilities.

Example:
    ```python
    from forge_shared.utils import get_client_ip, is_valid_ip

    ip = get_client_ip(request)
    if is_valid_ip(ip):
        print(f"Client IP: {ip}")
    ```
"""

from forge_shared.utils.ip import get_client_ip, is_valid_ip, is_private_ip
from forge_shared.utils.headers import (
    get_user_agent,
    parse_accept_header,
    get_bearer_token,
)

__all__ = [
    "get_client_ip",
    "is_valid_ip",
    "is_private_ip",
    "get_user_agent",
    "parse_accept_header",
    "get_bearer_token",
]
