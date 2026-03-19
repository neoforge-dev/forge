"""
Example FastAPI application demonstrating the forge-shared middleware registry.

Shows how to register, configure, and apply middleware using the
centralized registry pattern.

Run with: python -m forge_shared.middleware.example_app
"""

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from forge_shared.middleware import (
    MiddlewareRegistry,
    register_rate_limit,
    register_security,
    register_cors,
    register_request_id,
    register_logging,
    apply_standard_stack,
    configure_backend
)

def create_app() -> FastAPI:
    """Create FastAPI app with middleware registry pattern."""
    
    # Create middleware registry
    registry = MiddlewareRegistry()
    
    # Configure backend based on environment
    import os
    env = os.getenv("FORGE_ENV", "development")
    
    if env == "production":
        configure_backend("production", redis_url="redis://prod.example.com:6379")
        configure_backend("rate_limit", requests_per_minute=60)
    elif env == "testing":
        configure_backend("testing", requests_per_minute=1000)
    else:
        configure_backend("development", requests_per_minute=100)
    
    # Register middleware components
    register_request_id()
    register_cors(allow_origins=["*"])  # Permissive for development
    register_rate_limit(redis_url="redis://localhost:6379")
    register_security(
        allowed_origins=["https://app.example.com"],
        allowed_methods=["GET", "POST", "PUT", "DELETE"]
    )
    # Register structured logging middleware
    register_logging(
        log_level="DEBUG" if env == "development" else "INFO",
        environment=env,
        exclude_paths=["/health", "/metrics"],
    )
    
    # Create FastAPI app
    app = FastAPI(
        title="FORGE Middleware Example",
        description="Demonstrates forge-shared middleware registry",
        version="1.0.0"
    )
    
    # Apply middleware stack
    apply_standard_stack(app)
    
    return app

# Create app instance
app = create_app()

@app.get("/")
async def root():
    """Root endpoint demonstrating middleware effects."""
    return JSONResponse({
        "message": "FORGE Shared Middleware Example",
        "features": [
            "Request ID tracking",
            "CORS handling", 
            "Rate limiting",
            "Security headers",
            "Exception handling"
        ],
        "middleware": [
            "request_id",
            "cors", 
            "rate_limit",
            "security",
            "exception_handler"
        ]
    })

@app.get("/health")
async def health():
    """Health check endpoint."""
    return JSONResponse({"status": "healthy"})

if __name__ == "__main__":
    import uvicorn
    
    print("🚀 Starting FORGE Middleware Example App")
    print("📊 Registered middleware: request_id, cors, rate_limit, security, exception_handler")
    print("🔧 Environment: development")
    print("🌐 Available at: http://localhost:8000")
    print("📖 Health check: http://localhost:8000/health")
    
    uvicorn.run(app, host="0.0.0.0", port=8000)