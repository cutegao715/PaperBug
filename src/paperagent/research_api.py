"""Compatibility entry point for the versioned research router."""
from paperagent.api.v1.research import *  # retain existing request models
from paperagent.api.v1.research import create_router


def register_research_routes(app, resolve_ws):
    router = create_router(resolve_ws)
    if app is not None:
        app.include_router(router, prefix="/api")
    return router
