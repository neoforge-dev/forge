"""
Structured logging middleware for forge-shared.

Provides centralized structured JSON logging with:
- Request correlation IDs
- Service name and version tracking
- Log level filtering per environment
- Performance metrics
- Integration with existing FORGE monitoring

Example:
    ```python
    from fastapi import FastAPI
    from forge_shared.middleware import MiddlewareRegistry, register_logging

    registry = MiddlewareRegistry()
    register_logging(log_level="INFO", environment="production")
    
    app = FastAPI()
    registry.apply_to_app(app)
    ```

Or use directly:
    ```python
    app.add_middleware(
        StructuredLoggingMiddleware,
        service_name="my-service",
        log_level="INFO"
    )
    """
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional, Union

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

# Import request context for correlation IDs - with fallback
try:
    from forge_shared.logging.context import get_request_context, set_correlation_id
    _CONTEXT_AVAILABLE = True
except ImportError:
    # Fallback if context module not available
    _CONTEXT_AVAILABLE = False
    
    def get_request_context() -> Dict[str, Any]:
        return {}
    
    def set_correlation_id(correlation_id: str) -> None:
        pass


# Service metadata - populated by setup.py at runtime
SERVICE_NAME = os.getenv("FORGE_SERVICE_NAME", "forge-shared")
SERVICE_VERSION = os.getenv("FORGE_SERVICE_VERSION", "2.1.1")


class StructuredLoggingMiddleware(BaseHTTPMiddleware):
    """
    Structured JSON logging middleware for HTTP requests/responses.
    
    Features:
    - Structured JSON format for log aggregation
    - Request correlation IDs for distributed tracing
    - Automatic timing and performance metrics
    - Configurable log levels per environment
    - Service metadata tagging
    - Sanitization of sensitive data
    
    Attributes:
        app: ASGI application (callable: Any)
        service_name: Name of the service for log tagging
        service_version: Version of the service
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        environment: Environment name (development, staging, production)
        exclude_paths: Paths to exclude from logging
        exclude_headers: Headers to exclude from logs
        sanitize_fields: Field names to sanitize (e.g., passwords, tokens)
        include_request_body: Whether to log request bodies
        include_response_body: Whether to log response bodies
        performance_threshold_ms: Log slow requests above this threshold
    
    Example:
        ```python
        app.add_middleware(
            StructuredLoggingMiddleware,
            service_name="my-api",
            environment="production",
            exclude_paths=["/health", "/metrics"],
            log_level="INFO"
        )
        ```
    """
    
    def __init__(
        self,
        app: Any,
        service_name: str = SERVICE_NAME,
        service_version: str = SERVICE_VERSION,
        log_level: Union[str, int] = logging.INFO,
        environment: Optional[str] = None,
        exclude_paths: Optional[list[str]] = None,
        exclude_headers: Optional[list[str]] = None,
        sanitize_fields: Optional[list[str]] = None,
        include_request_body: bool = False,
        include_response_body: bool = False,
        performance_threshold_ms: float = 1000.0,
    ) -> None:
        """
        Initialize structured logging middleware.
        
        Args:
            app: ASGI application (Any)
            service_name: Name of the service for log tagging
            service_version: Version of the service
            log_level: Logging level (str or int)
            environment: Environment name (defaults to FORGE_ENV or 'development')
            exclude_paths: Paths to exclude from logging
            exclude_headers: Headers to exclude from logs
            sanitize_fields: Field names to sanitize
            include_request_body: Whether to log request bodies
            include_response_body: Whether to log response bodies
            performance_threshold_ms: Threshold for slow request logging
        """
        super().__init__(app)
        
        self.service_name = service_name
        self.service_version = service_version
        self.log_level = logging.getLevelName(log_level) if isinstance(log_level, str) else log_level
        self.environment = environment or os.getenv("FORGE_ENV", "development")
        self.exclude_paths = exclude_paths or ["/health", "/metrics", "/favicon.ico"]
        self.exclude_headers = exclude_headers or [
            "authorization",
            "x-api-key",
            "cookie",
            "set-cookie",
        ]
        self.sanitize_fields = sanitize_fields or [
            "password",
            "token",
            "secret",
            "api_key",
            "access_token",
            "refresh_token",
        ]
        self.include_request_body = include_request_body
        self.include_response_body = include_response_body
        self.performance_threshold_ms = performance_threshold_ms
        
        # Create logger
        self.logger = logging.getLogger(f"forge.{service_name}")
        self.logger.setLevel(self.log_level)
        
        # Ensure handler exists
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(JSONFormatter(service_name, service_version))
            self.logger.addHandler(handler)
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """
        Process request with structured logging.
        
        Args:
            request: Incoming request
            call_next: Next middleware or route handler
            
        Returns:
            Response from next handler
        """
        # Skip excluded paths
        if self._should_skip(request):
            return await call_next(request)
        
        # Generate or extract correlation ID
        correlation_id = self._get_correlation_id(request)
        set_correlation_id(correlation_id)
        
        # Start timing
        start_time = time.perf_counter()
        start_timestamp = datetime.now(timezone.utc)
        
        # Prepare request log data
        request_data = self._prepare_request_data(request)
        
        # Log request
        self._log_request(request_data, correlation_id)
        
        # Process request
        try:
            response = await call_next(request)
            response_body = None
            
            # Capture response body if enabled
            if self.include_response_body:
                response_body = await self._get_response_body(response)
            
        except Exception as exc:
            # Calculate duration on error
            duration_ms = (time.perf_counter() - start_time) * 1000
            
            # Log error
            self._log_error(
                request_data,
                correlation_id,
                duration_ms,
                str(exc),
                type(exc).__name__,
            )
            raise
        
        # Calculate duration
        duration_ms = (time.perf_counter() - start_time) * 1000
        duration_seconds = duration_ms / 1000.0
        
        # Prepare response log data
        response_data = self._prepare_response_data(response, duration_ms, response_body)
        
        # Log response
        self._log_response(request_data, response_data, correlation_id, duration_seconds)
        
        return response
    
    def _should_skip(self, request: Request) -> bool:
        """Check if path should be excluded from logging."""
        return any(
            request.url.path.startswith(path)
            for path in self.exclude_paths
        )
    
    def _get_correlation_id(self, request: Request) -> str:
        """Get correlation ID from header or generate new one."""
        # Try to get from header
        correlation_id = request.headers.get("X-Correlation-ID") or request.headers.get(
            "X-Request-ID"
        )
        
        if not correlation_id:
            # Generate new ID
            import uuid
            correlation_id = str(uuid.uuid4())
        
        return correlation_id
    
    def _prepare_request_data(self, request: Request) -> Dict[str, Any]:
        """Prepare request data for logging."""
        # Build headers dict (excluding sensitive ones)
        headers = {}
        for key, value in request.headers.items():
            if key.lower() not in self.exclude_headers:
                headers[key] = value
        
        data = {
            "event": "request",
            "method": request.method,
            "path": request.url.path,
            "query_string": request.url.query,
            "headers": headers,
            "client_ip": self._get_client_ip(request),
            "user_agent": request.headers.get("user-agent"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        
        return data
    
    async def _capture_request_body(self, request: Request) -> Optional[Dict[str, Any]]:
        """Capture request body if enabled."""
        if not self.include_request_body:
            return None
        
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            try:
                body = await request.body()
                if body:
                    return json.loads(body)
            except (json.JSONDecodeError, Exception):
                pass
        return None
    
    async def _prepare_request_data_async(self, request: Request) -> Dict[str, Any]:
        """Prepare request data for logging (async version with body)."""
        # Build headers dict (excluding sensitive ones)
        headers = {}
        for key, value in request.headers.items():
            if key.lower() not in self.exclude_headers:
                headers[key] = value
        
        data = {
            "event": "request",
            "method": request.method,
            "path": request.url.path,
            "query_string": request.url.query,
            "headers": headers,
            "client_ip": self._get_client_ip(request),
            "user_agent": request.headers.get("user-agent"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        
        # Add request body if enabled and present
        body = await self._capture_request_body(request)
        if body:
            data["body"] = self._sanitize(body)
        
        return data
    
    async def _get_request_body(self, request: Request) -> Optional[Dict[str, Any]]:
        """Get request body as JSON if possible."""
        try:
            body = await request.body()
            if body:
                return json.loads(body)
        except (json.JSONDecodeError, Exception):
            pass
        return None
    
    def _prepare_response_data(
        self,
        response: Response,
        duration_ms: float,
        body: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Prepare response data for logging."""
        data = {
            "event": "response",
            "status_code": response.status_code,
            "duration_ms": round(duration_ms, 2),
            "slow_request": duration_ms > self.performance_threshold_ms,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        
        # Add response headers (excluding sensitive ones)
        headers = {}
        for key, value in response.headers.items():
            if key.lower() not in self.exclude_headers:
                headers[key] = value
        data["headers"] = headers
        
        # Add response body if enabled
        if self.include_response_body and body:
            data["body"] = self._sanitize(body) if isinstance(body, dict) else body
        
        return data
    
    def _get_client_ip(self, request: Request) -> Optional[str]:
        """Extract client IP address."""
        # Check for forwarded headers
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        
        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip
        
        return request.client.host if request.client else None
    
    async def _get_response_body(self, response: Response) -> Optional[Any]:
        """Get response body for logging."""
        # Access body attribute if available
        body = getattr(response, "body", None)
        if body:
            try:
                return json.loads(body)
            except (json.JSONDecodeError, UnicodeDecodeError):
                return "<binary or non-JSON>"
        return None
    
    def _sanitize(self, data: Any) -> Any:
        """Sanitize sensitive fields in data."""
        if isinstance(data, dict):
            sanitized = {}
            for key, value in data.items():
                if key.lower() in [f.lower() for f in self.sanitize_fields]:
                    sanitized[key] = "<REDACTED>"
                elif isinstance(value, (dict, list)):
                    sanitized[key] = self._sanitize(value)
                else:
                    sanitized[key] = value
            return sanitized
        elif isinstance(data, list):
            return [self._sanitize(item) for item in data]
        return data
    
    def _log_request(
        self,
        request_data: Dict[str, Any],
        correlation_id: str,
    ) -> None:
        """Log incoming request."""
        log_data = {
            **request_data,
            "correlation_id": correlation_id,
            "service": self.service_name,
            "version": self.service_version,
            "environment": self.environment,
        }
        self.logger.info("Incoming request", extra=log_data)
    
    def _log_response(
        self,
        request_data: Dict[str, Any],
        response_data: Dict[str, Any],
        correlation_id: str,
        duration_seconds: float,
    ) -> None:
        """Log outgoing response."""
        log_data = {
            **response_data,
            "request": {
                "method": request_data.get("method"),
                "path": request_data.get("path"),
            },
            "correlation_id": correlation_id,
            "service": self.service_name,
            "version": self.service_version,
            "environment": self.environment,
            "duration_seconds": round(duration_seconds, 4),
        }
        
        # Use appropriate log level based on status code
        if response_data.get("slow_request"):
            self.logger.warning("Slow request detected", extra=log_data)
        elif response_data.get("status_code", 200) >= 400:
            self.logger.warning("Request completed with error", extra=log_data)
        else:
            self.logger.info("Request completed", extra=log_data)
    
    def _log_error(
        self,
        request_data: Dict[str, Any],
        correlation_id: str,
        duration_ms: float,
        error_message: str,
        error_type: str,
    ) -> None:
        """Log error occurred during request processing."""
        log_data = {
            **request_data,
            "correlation_id": correlation_id,
            "service": self.service_name,
            "version": self.service_version,
            "environment": self.environment,
            "duration_ms": round(duration_ms, 2),
            "error": {
                "type": error_type,
                "message": error_message,
            },
        }
        self.logger.exception("Request failed", extra=log_data)


class JSONFormatter(logging.Formatter):
    """
    JSON log formatter for structured logging.
    
    Outputs log records as JSON objects for easy parsing
    by log aggregation systems like ELK, Datadog, etc.
    """
    
    def __init__(
        self,
        service_name: str,
        service_version: str,
        environment: Optional[str] = None,
    ) -> None:
        """Initialize JSON formatter."""
        super().__init__()
        self.service_name = service_name
        self.service_version = service_version
        self.environment = environment or os.getenv("FORGE_ENV", "development")
    
    def format(self, record: logging.LogRecord) -> str:
        """
        Format log record as JSON.
        
        Args:
            record: Log record to format
            
        Returns:
            JSON string representation of the log
        """
        log_data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
            "service": self.service_name,
            "version": self.service_version,
            "environment": self.environment,
            "logger": record.name,
        }
        
        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        
        # Add extra attributes
        extra = getattr(record, "extra", {})
        for key, value in extra.items():
            # Skip keys that might conflict with standard log fields
            if key not in ["timestamp", "level", "message", "service", "version", "environment", "logger"]:
                log_data[key] = value
        
        return json.dumps(log_data, default=str)


# Convenience function for registry
def create_logging_middleware(
    service_name: str = SERVICE_NAME,
    log_level: str = "INFO",
    environment: Optional[str] = None,
    **kwargs,
) -> StructuredLoggingMiddleware:
    """
    Create logging middleware instance with common defaults.
    
    Args:
        service_name: Name of the service
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        environment: Environment name
        **kwargs: Additional arguments for StructuredLoggingMiddleware
        
    Returns:
        Configured StructuredLoggingMiddleware instance
    """
    return StructuredLoggingMiddleware(
        app=None,  # Will be set when added to app
        service_name=service_name,
        log_level=log_level,
        environment=environment,
        **kwargs
    )


# Alias for backward compatibility with __init__.py
LoggingMiddleware = StructuredLoggingMiddleware
