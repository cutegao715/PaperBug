"""Compatibility entry point for the versioned project router."""
from paperagent.api.v1.project import *  # retain existing request models
from paperagent.api.v1.project import create_router


def register_project_routes(app, resolve_ws, idle):
    router = create_router(resolve_ws, idle)
    if app is not None:
        app.include_router(router, prefix="/api")
    return router
