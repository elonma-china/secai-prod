"""HTTP layer: request schemas, SSE encoding, message builders, middleware and routes."""

from .middleware import configure_middleware
from .routes import register_routes

__all__ = ["configure_middleware", "register_routes"]
