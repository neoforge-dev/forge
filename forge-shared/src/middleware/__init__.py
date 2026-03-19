"""
Service Specification: Middleware Stack

This file defines the interface for the FORGE middleware registry and components.
Implementation: forge_shared.middleware
"""

from typing import List, Optional, Any

class MiddlewareRegistryInterface:
    """Registry for managing FastAPI middleware."""
    
    def register_rate_limit(self, redis_url: str, requests_per_minute: int = 60, **kwargs):
        """Register rate limiting middleware."""
        pass

    def register_security(self, **kwargs):
        """Register security headers middleware."""
        pass

    def register_cors(self, allow_origins: List[str] = ["*"], **kwargs):
        """Register CORS middleware."""
        pass

    def register_request_id(self, **kwargs):
        """Register request ID middleware."""
        pass

    def apply_to_app(self, app: Any):
        """Apply registered middleware to a FastAPI app."""
        pass
