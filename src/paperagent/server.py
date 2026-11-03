"""Local HTTP app assembly. Business state lives in api.control."""
from fastapi import FastAPI
from paperagent.api import control
from paperagent.api.compat import LegacyAPIDeprecation
from paperagent.api.v1 import build_routers

app = FastAPI(title="PaperAgent Control Service")
app.router.add_event_handler("startup", control.start_scheduler)
app.router.add_event_handler("shutdown", control.stop_scheduler)
from paperagent.tools.mcp_bridge import close_all
app.router.add_event_handler("shutdown", close_all)
app.add_middleware(LegacyAPIDeprecation)
v1_router, internal_router = build_routers(control)
app.include_router(v1_router, prefix="/api/v1")
app.include_router(v1_router, prefix="/api", deprecated=True)
app.include_router(internal_router, prefix="/internal")
app.include_router(internal_router, prefix="/api", deprecated=True)

app.add_api_route('/', control.index, methods=['GET'])
app.add_api_route('/lifecycle.js', control.lifecycle_script, methods=['GET'])
app.add_api_route('/manuscript.js', control.manuscript_script, methods=['GET'])
app.add_api_route('/paper-review.js', control.paper_review_script, methods=['GET'])
app.add_api_route('/research.js', control.research_script, methods=['GET'])
app.add_api_route('/characters/{action}.png', control.character_sprite, methods=['GET'])
app.add_api_route('/v6/{name}', control.frontend_v6_asset, methods=['GET'])
app.add_api_route('/classic', control.classic_frontend, methods=['GET'])


def __getattr__(name):
    # Preserve read access to the old internal helpers and RUNS for CLI/plugins.
    return getattr(control, name)

