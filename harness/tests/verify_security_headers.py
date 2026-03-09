#!/usr/bin/env python3
"""Manual verification script for security headers middleware.

This script starts the webhook server and tests that security headers are present.
Run this instead of pytest if you want a quick verification.

Usage:
    python tests/verify_security_headers.py
"""

import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))


def verify_security_headers():
    """Verify security headers are present in responses."""
    try:
        from fastapi.testclient import TestClient

        from forge_harness.webhook_server import AuthConfig, create_app
    except ImportError as e:
        print(f"Error: {e}")
        print("Install FastAPI: uv add fastapi")
        return False

    print("Creating test app...")
    auth_config = AuthConfig(require_auth=False)
    app = create_app(auth_config=auth_config)

    if app is None:
        print("Error: Could not create app (FastAPI not installed?)")
        return False

    print("Creating test client...")
    client = TestClient(app)

    print("\nTesting /health endpoint...")
    response = client.get("/health")

    print(f"Status code: {response.status_code}")
    print("\nResponse headers:")

    # Check for security headers
    expected_headers = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "X-XSS-Protection": "1; mode=block",
        "Referrer-Policy": "strict-origin-when-cross-origin",
    }

    all_passed = True
    for header, expected_value in expected_headers.items():
        actual_value = response.headers.get(header)
        if actual_value == expected_value:
            print(f"✓ {header}: {actual_value}")
        else:
            print(f"✗ {header}: Expected '{expected_value}', got '{actual_value}'")
            all_passed = False

    # HSTS should NOT be present for HTTP
    if "Strict-Transport-Security" in response.headers:
        print("✗ Strict-Transport-Security: Should not be present for HTTP requests")
        all_passed = False
    else:
        print("✓ Strict-Transport-Security: Correctly omitted for HTTP")

    # CSP should NOT be present
    if "Content-Security-Policy" in response.headers:
        print("✗ Content-Security-Policy: Should not be present (breaks frontend)")
        all_passed = False
    else:
        print("✓ Content-Security-Policy: Correctly omitted")

    print("\n" + "=" * 60)
    if all_passed:
        print("✓ All security headers verified successfully!")
        return True
    else:
        print("✗ Some security header checks failed")
        return False


if __name__ == "__main__":
    success = verify_security_headers()
    sys.exit(0 if success else 1)
