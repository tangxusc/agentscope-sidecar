"""HTTP 路由 + CLI 命令。

路由路径是**完整路径**,底座不加前缀。冲突在启动时就会失败,不会静默覆盖。

跑::

    cd examples/04-routes && sidecar --mode http --port 8000
    curl --noproxy '*' http://127.0.0.1:8000/status
"""

from __future__ import annotations

from pathlib import Path

from starlette.requests import Request
from starlette.responses import JSONResponse

from agentscope_sidecar import Effect

LOG = Path(__file__).parent.parent / "commands.log"


async def status(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "uptime": "假装有"})


async def echo(request: Request) -> JSONResponse:
    body = await request.json()
    return JSONResponse({"echo": body}, status_code=201)


def show_status(args: str) -> None:
    """CLI 子命令的处理函数:``(args: str) -> None``。"""
    LOG.write_text(f"status:{args}", encoding="utf-8")


async def setup(routes, commands) -> Effect:
    routes.add("GET", "/status", status)
    routes.add("POST", "/echo", echo)
    commands.add("status", show_status)

    def cleanup() -> None:
        routes.remove("GET", "/status")
        routes.remove("POST", "/echo")
        commands.remove("status")
        LOG.unlink(missing_ok=True)

    return cleanup
