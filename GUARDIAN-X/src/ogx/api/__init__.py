"""Authenticated REST API and dashboard (ENT-08, UI-04, UI-05).

Importing this package has no side effects: :func:`ogx.api.server.create_server`
creates the datastore and binds a socket, and the dashboard assets are plain
strings served by that server.
"""

from .server import (
    API_VERSION,
    ApiContext,
    ApiError,
    ApiServer,
    MAX_BODY_BYTES,
    RawResponse,
    create_server,
    serve,
)

__all__ = [
    "API_VERSION",
    "ApiContext",
    "ApiError",
    "ApiServer",
    "MAX_BODY_BYTES",
    "RawResponse",
    "create_server",
    "serve",
]
