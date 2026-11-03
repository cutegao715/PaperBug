"""Compose stable domain routers; keep diagnostics outside the stable surface."""
from fastapi import APIRouter
from . import project, lifecycle, manuscript, review, research, connections, conversation, runtime, sync


def build_routers(service):
    public, internal = APIRouter(), APIRouter()
    # Lifecycle's factory composes the existing domain factories with one idle guard.
    domains = lifecycle.create_router(service._ws, service.RUNS)
    for route in domains.routes:
        target = internal if getattr(route, "path", None) == "/projects/{project_id}/context" else public
        target.routes.append(route)
    for module in (project, manuscript, review, research, connections, conversation, runtime, sync):
        public.include_router(module.control_router(service))
    return public, internal

