"""Compatibility entry point for the versioned lifecycle router."""
from paperagent.api.v1.lifecycle import *  # retain existing request models
from paperagent.api.v1.lifecycle import create_router


def register_lifecycle_routes(app, resolve_ws, runs):
    router = create_router(resolve_ws, runs)
    if app is not None:
        app.include_router(router, prefix="/api")
    return router
