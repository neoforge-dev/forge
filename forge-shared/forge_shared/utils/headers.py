"""
HTTP header utility functions.

Provides functions for parsing and extracting information from HTTP headers.

Example:
    ```python
    from forge_shared.utils.headers import get_user_agent, get_bearer_token

    user_agent = get_user_agent(request)
    token = get_bearer_token(request)
    ```
"""

from typing import Optional
from fastapi import Request


def get_bearer_token(request: Request) -> Optional[str]:
    """
    Extract Bearer token from Authorization header.

    Args:
        request: FastAPI request object

    Returns:
        Bearer token or None

    Example:
        ```python
        token = get_bearer_token(request)
        if token:
            print(f"Token: {token}")
        ```
    """
    authorization = request.headers.get("Authorization")
    if authorization and authorization.startswith("Bearer "):
        return authorization.split(" ", 1)[1]
    return None


def get_user_agent(request: Request) -> Optional[str]:
    """
    Extract User-Agent header.

    Args:
        request: FastAPI request object

    Returns:
        User-Agent string or None

    Example:
        ```python
        user_agent = get_user_agent(request)
        print(f"User-Agent: {user_agent}")
        ```
    """
    return request.headers.get("User-Agent")


def parse_accept_header(accept: Optional[str]) -> list[dict[str, str | float]]:
    """
    Parse Accept header and return sorted list of media types.

    Args:
        accept: Accept header value (can be None)

    Returns:
        List of dicts with type, subtype, and q value

    Example:
        ```python
        accept = "text/html,application/json;q=0.9,*/*;q=0.8"
        types = parse_accept_header(accept)
        ```
    """
    if not accept:
        return []

    media_types = []

    for part in accept.split(","):
        part = part.strip()
        if not part:
            continue

        # Parse media type and q value
        media_type = part
        q = 1.0

        if ";q=" in part:
            media_type, q_str = part.split(";q=", 1)
            try:
                q = float(q_str)
            except ValueError:
                q = 1.0

        # Parse type and subtype
        if "/" in media_type:
            type_, subtype = media_type.split("/", 1)
        else:
            type_, subtype = media_type, "*"

        media_types.append(
            {
                "type": type_,
                "subtype": subtype,
                "q": q,
                "full": media_type,
            }
        )

    # Sort by q value (descending)
    media_types.sort(key=lambda x: x["q"], reverse=True)

    return media_types


def get_content_type(request: Request) -> Optional[str]:
    """
    Extract Content-Type header.

    Args:
        request: FastAPI request object

    Returns:
        Content-Type string or None

    Example:
        ```python
        content_type = get_content_type(request)
        if content_type == "application/json":
            print("JSON request")
        ```
    """
    content_type = request.headers.get("Content-Type")
    if content_type:
        # Remove charset and other parameters
        return content_type.split(";")[0].strip()
    return None


def get_accept_language(request: Request) -> Optional[str]:
    """
    Extract Accept-Language header.

    Args:
        request: FastAPI request object

    Returns:
        Accept-Language string or None

    Example:
        ```python
        lang = get_accept_language(request)
        print(f"Language: {lang}")
        ```
    """
    return request.headers.get("Accept-Language")


def parse_accept_language(accept_language: str) -> list[dict[str, str | float]]:
    """
    Parse Accept-Language header.

    Args:
        accept_language: Accept-Language header value

    Returns:
        List of dicts with language and q value

    Example:
        ```python
        accept = "en-US,en;q=0.9,es;q=0.8"
        langs = parse_accept_language(accept)
        ```
    """
    languages = []

    for part in accept_language.split(","):
        part = part.strip()
        if not part:
            continue

        # Parse language and q value
        lang = part
        q = 1.0

        if ";q=" in part:
            lang, q_str = part.split(";q=", 1)
            try:
                q = float(q_str)
            except ValueError:
                q = 1.0

        languages.append({"language": lang, "q": q})

    # Sort by q value (descending)
    languages.sort(key=lambda x: x["q"], reverse=True)

    return languages
