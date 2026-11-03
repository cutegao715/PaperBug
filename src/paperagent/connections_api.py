"""Compatibility entry point for the versioned connections router."""
from paperagent.api.v1.connections import *  # retain existing request models
from paperagent.api.v1.connections import create_router


def register_connection_routes(app, resolve_ws, idle):
    router = create_router(resolve_ws, idle)
    if app is not None:
        app.include_router(router, prefix="/api")
    return router
