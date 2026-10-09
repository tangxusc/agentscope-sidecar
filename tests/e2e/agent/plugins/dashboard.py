"""HTTP 路由。"""

from __future__ import annotations

from starlette.responses import JSONResponse

from agentscope_sidecar import Context


async def index(request):
    return JSONResponse({"page": "index"})


async def stats(request):
    return JSONResponse({"page": "stats"})


async def create(request):
    return JSONResponse({"created": True}, status_code=201)


async def setup(routes, ctx: Context) -> None:
    routes.add("GET", "/", index)
    routes.add("GET", "/stats", stats)
    routes.add("POST", "/stats", create)
    ctx.logger.info("dashboard 路由已注册")
