"""Legacy-prefix deprecation without buffering responses (including SSE)."""
from starlette.datastructures import MutableHeaders


class LegacyAPIDeprecation:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        legacy = path.startswith("/api/") and not path.startswith("/api/v1/")
        async def send_with_header(message):
            if legacy and message["type"] == "http.response.start":
                MutableHeaders(scope=message)["Deprecation"] = "@1793404800"
            await send(message)
        await self.app(scope, receive, send_with_header)
