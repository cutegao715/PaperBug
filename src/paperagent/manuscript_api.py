"""Compatibility entry point for the versioned manuscript router."""
from paperagent.api.v1.manuscript import *  # retain existing request models
from paperagent.api.v1.manuscript import create_router


def register_manuscript_routes(app, resolve_ws, idle):
    router = create_router(resolve_ws, idle)
    if app is not None:
        app.include_router(router, prefix="/api")
    return router
