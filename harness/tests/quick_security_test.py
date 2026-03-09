#!/usr/bin/env python3
"""Quick inline test for security headers - no external dependencies on test framework state."""

if __name__ == "__main__":
    import sys
    from pathlib import Path

    # Add parent to path
    sys.path.insert(0, str(Path(__file__).parent.parent))

    try:
        # Import at runtime to avoid import errors
        from fastapi.testclient import TestClient

        from forge_harness.webhook_server import AuthConfig, create_app

        print("Creating app with auth disabled...")
        app = create_app(auth_config=AuthConfig(require_auth=False))

        if not app:
            print("ERROR: App creation returned None")
            sys.exit(1)

        print("Creating test client...")
        client = TestClient(app)

        print("Making request to /health...")
        response = client.get("/health")

        print(f"\nStatus: {response.status_code}")
        print("\nSecurity Headers:")
        print("-" * 60)

        headers_to_check = [
            "X-Content-Type-Options",
            "X-Frame-Options",
            "X-XSS-Protection",
            "Referrer-Policy",
            "Strict-Transport-Security",
            "Content-Security-Policy",
        ]

        for header in headers_to_check:
            value = response.headers.get(header, "NOT PRESENT")
            symbol = "✓" if value != "NOT PRESENT" else "○"
            print(f"{symbol} {header}: {value}")

        print("\n" + "=" * 60)

        # Verify expected headers
        expected = {
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "X-XSS-Protection": "1; mode=block",
            "Referrer-Policy": "strict-origin-when-cross-origin",
        }

        all_good = True
        for header, expected_val in expected.items():
            actual = response.headers.get(header)
            if actual != expected_val:
                print(f"✗ FAIL: {header} = '{actual}', expected '{expected_val}'")
                all_good = False

        # HSTS should NOT be present for HTTP
        if "Strict-Transport-Security" in response.headers:
            print("✗ FAIL: HSTS should not be set for HTTP requests")
            all_good = False

        # CSP should NOT be present
        if "Content-Security-Policy" in response.headers:
            print("✗ FAIL: CSP should not be set (breaks frontend)")
            all_good = False

        if all_good:
            print("\n✓ SUCCESS: All security headers correct!")
            sys.exit(0)
        else:
            print("\n✗ FAILURE: Some checks failed")
            sys.exit(1)

    except ImportError as e:
        print(f"Import error: {e}")
        print("Install dependencies: uv add fastapi")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
