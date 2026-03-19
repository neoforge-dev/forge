"""
Middleware registry pattern for managing multiple middleware instances.

Provides a centralized way to register, configure, and manage
middleware across FastAPI applications with support for different
backend implementations and dynamic configuration.

Example:
    ```python
    from forge_shared.middleware import MiddlewareRegistry
    from forge_shared.middleware.rate_limit import RateLimitMiddleware
    from forge_shared.middleware.security import SecurityMiddleware
    
    registry = MiddlewareRegistry()
    
    # Register middleware with configuration
    registry.register("rate_limit", RateLimitMiddleware)(
        redis_url="redis://localhost:6379",
        requests_per_minute=100
    )
    
    registry.register("security", SecurityMiddleware)(
        allowed_origins=["https://app.example.com"]
    )
    
    # Apply to FastAPI app
    app = FastAPI()
    for middleware in registry.get_middleware():
        app.add_middleware(middleware.cls, **middleware.kwargs)
    ```

The registry supports:
- Named middleware registration
- Configuration passing via kwargs
- Ordered middleware application
- Backend-specific middleware (memory, Redis, custom)
- Dependency injection for FastAPI dependencies
"""

from .base import BaseMiddleware
from typing import Any, Callable, Dict, List, Optional, Type, Union
from dataclasses import dataclass, field


@dataclass
class MiddlewareConfig:
    """Configuration for a middleware instance."""
    cls: Type[BaseMiddleware]
    kwargs: Dict[str, Any] = field(default_factory=dict)
    order: int = 100  # Higher numbers = later execution


class MiddlewareRegistry:
    """
    Registry for managing FastAPI middleware configurations.
    
    Provides centralized registration, configuration, and ordered
    application of middleware with support for different backend types.
    """
    
    def __init__(self) -> None:
        """Initialize the middleware registry."""
        self._configs: Dict[str, MiddlewareConfig] = {}
        self._backend_configs: Dict[str, Any] = {}
    
    def register(
        self, 
        name: str, 
        middleware_cls: Type[BaseMiddleware], 
        order: Optional[int] = None,
        **kwargs
    ) -> None:
        """
        Register a middleware with configuration.
        
        Args:
            name: Unique identifier for the middleware
            middleware_cls: Middleware class to instantiate
            order: Execution order (lower = earlier)
            **kwargs: Configuration parameters for the middleware
        """
        if name in self._configs:
            raise ValueError(f"Middleware '{name}' already registered")
        
        config = MiddlewareConfig(
            cls=middleware_cls,
            kwargs=kwargs,
            order=order or len(self._configs) * 10
        )
        
        self._configs[name] = config
    
    def register_backend(self, name: str, config: Any) -> None:
        """
        Register a backend configuration for middleware.
        
        Used to share configuration like Redis URLs across
        multiple middleware instances.
        """
        self._backend_configs[name] = config
    
    def get_config(self, name: str) -> Optional[MiddlewareConfig]:
        """Get middleware configuration by name."""
        return self._configs.get(name)
    
    def get_backend_config(self, name: str) -> Any:
        """Get backend configuration by name."""
        return self._backend_configs.get(name)
    
    def get_middleware(
        self, 
        names: Optional[List[str]] = None,
        backend: str = "default"
    ) -> List[MiddlewareConfig]:
        """
        Get middleware configurations in execution order.
        
        Args:
            names: Optional list of middleware names to include
            backend: Backend configuration name to use
        
        Returns:
            List of middleware configurations sorted by order
        """
        configs = []
        
        for name, config in self._configs.items():
            if names is None or name in names:
                # Apply backend configuration if available
                backend_config = self._backend_configs.get(f"{name}_{backend}")
                if backend_config:
                    # Merge backend-specific config with middleware config
                    merged_kwargs = {**config.kwargs, **backend_config}
                    configs.append(MiddlewareConfig(
                        cls=config.cls,
                        kwargs=merged_kwargs,
                        order=config.order
                    ))
                else:
                    configs.append(config)
        
        # Sort by order (lower = earlier execution)
        configs.sort(key=lambda c: c.order)
        return configs
    
    def create_middleware(
        self, 
        config: MiddlewareConfig
    ) -> BaseMiddleware:
        """
        Create a middleware instance from configuration.
        
        Args:
            config: Middleware configuration
            
        Returns:
            Instantiated middleware
        """
        return config.cls(**config.kwargs)
    
    def apply_to_app(
        self,
        app: Any,  # FastAPI instance
        names: Optional[List[str]] = None,
        backend: str = "default"
    ) -> None:
        """
        Apply registered middleware to a FastAPI app.

        Args:
            app: FastAPI application instance
            names: Optional list of middleware names to include
            backend: Backend configuration name
        """
        middleware_configs = self.get_middleware(names=names, backend=backend)

        for config in middleware_configs:
            app.add_middleware(config.cls, **config.kwargs)
    
    # Alias for backwards compatibility
    def apply_all(self, app: Any) -> None:
        """Alias for apply_to_app()."""
        self.apply_to_app(app)
    
    def list_registered(self) -> List[str]:
        """Get list of registered middleware names."""
        return list(self._configs.keys())
    
    # Alias for backwards compatibility
    def list_middlewares(self) -> List[str]:
        """Alias for list_registered()."""
        return self.list_registered()
    
    def get(self, name: str) -> Optional[Type[BaseMiddleware]]:
        """
        Get middleware class by name.
        
        Args:
            name: Middleware name
            
        Returns:
            Middleware class or None if not found
        """
        config = self._configs.get(name)
        return config.cls if config else None
    
    def unregister(self, name: str) -> None:
        """Unregister a middleware."""
        self._configs.pop(name, None)
    
    def clear(self) -> None:
        """Clear all registered middleware."""
        self._configs.clear()
        self._backend_configs.clear()

    def register_rate_limit(
        self,
        redis_url: str = "redis://localhost:6379",
        requests_per_minute: int = 100,
        burst_size: int = 200,
        **kwargs
    ) -> None:
        """Register rate limiting middleware."""
        from forge_shared.middleware.rate_limit import RateLimitMiddleware
        self.register(
            "rate_limit",
            RateLimitMiddleware,
            order=10,
            redis_url=redis_url,
            requests_per_minute=requests_per_minute,
            burst_size=burst_size,
            **kwargs
        )

    def register_security(
        self,
        **kwargs
    ) -> None:
        """Register security middleware."""
        from .security import SecurityMiddleware
        self.register(
            "security",
            SecurityMiddleware,
            order=20,
            **kwargs
        )

    def register_cors(
        self,
        allow_origins: List[str] = ["*"],
        allow_methods: List[str] = ["*"],
        allow_headers: List[str] = ["*"],
        **kwargs
    ) -> None:
        """Register CORS middleware."""
        from .cors import CORSMiddleware
        self.register(
            "cors",
            CORSMiddleware,
            order=5,
            allow_origins=allow_origins,
            allow_methods=allow_methods,
            allow_headers=allow_headers,
            **kwargs
        )

    def register_request_id(self, **kwargs) -> None:
        """Register request ID middleware."""
        from .request_id import RequestIDMiddleware
        self.register(
            "request_id",
            RequestIDMiddleware,
            order=1,
            **kwargs
        )

    def register_exception_handler(self, **kwargs) -> None:
        """Register exception handler middleware."""
        from .exceptions import ExceptionHandlerMiddleware
        self.register(
            "exception_handler",
            ExceptionHandlerMiddleware,
            order=1000,
            **kwargs
        )

    def register_logging(
        self,
        log_level: str = "INFO",
        include_paths: Optional[List[str]] = None,
        exclude_paths: Optional[List[str]] = None,
        **kwargs
    ) -> None:
        """Register logging middleware."""
        from .logging import LoggingMiddleware
        self.register(
            "logging",
            LoggingMiddleware,
            order=50,
            log_level=log_level,
            include_paths=include_paths,
            exclude_paths=exclude_paths,
            **kwargs
        )


# Global registry instance
_registry = MiddlewareRegistry()


def get_registry() -> MiddlewareRegistry:
    """Get the global middleware registry."""
    return _registry


# Convenience functions for common patterns
def register_rate_limit(
    redis_url: str = "redis://localhost:6379",
    requests_per_minute: int = 100,
    burst_size: int = 200,
    **kwargs
) -> None:
    """Register rate limiting middleware with common defaults."""
    from forge_shared.middleware.rate_limit import RateLimitMiddleware
    
    _registry.register(
        "rate_limit",
        RateLimitMiddleware,
        order=10,  # Early in pipeline
        redis_url=redis_url,
        requests_per_minute=requests_per_minute,
        burst_size=burst_size,
        **kwargs
    )


def register_security(**kwargs) -> None:
    """Register security middleware with common defaults."""
    from .security import SecurityMiddleware

    _registry.register(
        "security",
        SecurityMiddleware,
        order=20,  # After rate limiting
        **kwargs
    )


def register_cors(
    allow_origins: List[str] = ["*"],
    allow_methods: List[str] = ["*"],
    allow_headers: List[str] = ["*"],
    **kwargs
) -> None:
    """Register CORS middleware with permissive defaults."""
    from .cors import CORSMiddleware
    
    _registry.register(
        "cors",
        CORSMiddleware,
        order=5,  # Very early
        allow_origins=allow_origins,
        allow_methods=allow_methods,
        allow_headers=allow_headers,
        **kwargs
    )


def register_request_id(**kwargs) -> None:
    """Register request ID middleware."""
    from .request_id import RequestIDMiddleware
    
    _registry.register(
        "request_id",
        RequestIDMiddleware,
        order=1,  # First
        **kwargs
    )


def register_exception_handler(**kwargs) -> None:
    """Register exception handler middleware."""
    from .exceptions import ExceptionHandlerMiddleware
    
    _registry.register(
        "exception_handler",
        ExceptionHandlerMiddleware,
        order=1000,  # Last
        **kwargs
    )


def register_logging(
    log_level: str = "INFO",
    include_paths: Optional[List[str]] = None,
    exclude_paths: Optional[List[str]] = None,
    **kwargs
) -> None:
    """Register logging middleware."""
    from .logging import LoggingMiddleware
    
    _registry.register(
        "logging",
        LoggingMiddleware,
        order=50,  # Late but before exception handling
        log_level=log_level,
        include_paths=include_paths,
        exclude_paths=exclude_paths,
        **kwargs
    )


def configure_backend(
    backend_name: str, 
    redis_url: Optional[str] = None,
    **config
) -> None:
    """
    Configure backend settings for middleware.
    
    Example:
        configure_backend("production", redis_url="redis://prod.example.com:6379")
        configure_backend("testing", requests_per_minute=1000)
    """
    _registry.register_backend(backend_name, config)


# Example usage patterns
def apply_standard_stack(app: Any, backend: str = "default") -> None:
    """Apply standard middleware stack to an app.
    
    Standard order: Request ID -> CORS -> Rate Limit -> Security -> Logging -> Exception Handler
    """
    standard_middleware = ["request_id", "cors", "rate_limit", "security", "logging", "exception_handler"]
    _registry.apply_to_app(app, names=standard_middleware, backend=backend)


def apply_minimal_stack(app: Any, backend: str = "default") -> None:
    """Apply minimal middleware stack.
    
    Minimal: Request ID -> CORS -> Exception Handler
    """
    minimal_middleware = ["request_id", "cors", "exception_handler"]
    _registry.apply_to_app(app, names=minimal_middleware, backend=backend)