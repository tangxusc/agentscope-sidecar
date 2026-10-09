"""``http`` 模式(设计文档 §11.6)。

基于 Starlette + uvicorn(AgentScope 的传递依赖,不引入新依赖)。

内置路由:``/health`` ``/info`` ``/prompt`` ``/events/{reply_id}`` ``/cancel``
插件路由:在 ``setup(routes)`` 里 ``routes.add("GET", "/path", handler)``,
与内置路由挂在同一个 app 上。
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import socket
import sys
import time
import uuid
from typing import TYPE_CHECKING, Any, AsyncIterator, Callable

from agentscope.message import UserMsg
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from ..errors import DeclarationError
from ._wire import event_to_wire, message_to_wire

if TYPE_CHECKING:  # pragma: no cover
    from ..app import Sidecar

logger = logging.getLogger("sidecar.http")

_END = object()
SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


class _Replies:
    """reply_id → 事件队列。用于 SSE 与 ``GET /events/{id}``。

    队列在流结束后删除 —— 常驻服务里不清理就是内存泄漏。
    """

    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue] = {}

    def open(self) -> str:
        reply_id = uuid.uuid4().hex[:12]
        self._queues[reply_id] = asyncio.Queue()
        return reply_id

    def push(self, reply_id: str, payload: dict[str, Any]) -> None:
        queue = self._queues.get(reply_id)
        if queue is not None:
            queue.put_nowait(payload)

    def close(self, reply_id: str) -> None:
        queue = self._queues.get(reply_id)
        if queue is not None:
            queue.put_nowait(_END)

    def discard(self, reply_id: str) -> None:
        self._queues.pop(reply_id, None)

    async def stream(self, reply_id: str) -> AsyncIterator[bytes]:
        queue = self._queues.get(reply_id)
        if queue is None:
            yield b'event: error\ndata: {"error":"unknown reply_id"}\n\n'
            return
        try:
            while True:
                item = await queue.get()
                if item is _END:
                    break
                yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n".encode()
        finally:
            self.discard(reply_id)                    # 流结束即回收


def build_app(sidecar: "Sidecar") -> Starlette:
    """组装 Starlette app:内置路由 + 插件路由。"""
    replies = _Replies()
    state: dict[str, Any] = {"active": None, "tasks": {}}

    async def health(request: Request) -> JSONResponse:
        records = list(sidecar.loader.records.values())
        loaded = sum(1 for r in records if r.state.value == "loaded")
        failed = sum(1 for r in records if r.state.value == "failed")
        return JSONResponse({"status": "ok",
                             "plugins": {"loaded": loaded, "failed": failed}})

    async def info(request: Request) -> JSONResponse:
        return JSONResponse(sidecar.report())

    async def run_reply(reply_id: str, text: str) -> None:
        try:
            agent = sidecar.build_agent()
            async for event in agent.reply_stream(
                UserMsg(name="user", content=text), yield_final_msg=True,
            ):
                if not hasattr(event, "type"):
                    replies.push(reply_id, {
                        "reply_id": reply_id, "type": "result",
                        **message_to_wire(event),
                    })
                    continue
                await sidecar.forward_event(event)
                replies.push(reply_id, event_to_wire(event, reply_id))
        except asyncio.CancelledError:
            replies.push(reply_id, {"reply_id": reply_id, "type": "cancelled"})
            raise
        except Exception as exc:
            replies.push(reply_id, {
                "reply_id": reply_id, "type": "error",
                "error": f"{type(exc).__name__}: {exc}",
            })
        finally:
            if state["active"] == reply_id:
                state["active"] = None
            state["tasks"].pop(reply_id, None)
            replies.close(reply_id)

    async def prompt(request: Request) -> Response:
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "body 必须是 JSON"}, status_code=400)

        text = (body or {}).get("text")
        if not isinstance(text, str) or not text:
            return JSONResponse({"error": "text 必须是非空字符串"}, status_code=400)
        if state["active"] is not None:
            return JSONResponse({"error": "已有进行中的 prompt"}, status_code=409)

        reply_id = replies.open()
        state["active"] = reply_id
        task = asyncio.ensure_future(run_reply(reply_id, text))
        state["tasks"][reply_id] = task

        accept = request.headers.get("accept", "")
        if "text/event-stream" in accept:
            return StreamingResponse(
                replies.stream(reply_id), media_type="text/event-stream",
                headers=SSE_HEADERS,
            )
        return JSONResponse({"reply_id": reply_id})

    async def events(request: Request) -> Response:
        reply_id = request.path_params["reply_id"]
        return StreamingResponse(
            replies.stream(reply_id), media_type="text/event-stream",
            headers=SSE_HEADERS,
        )

    async def cancel(request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except Exception:
            body = {}
        reply_id = (body or {}).get("reply_id")
        task = state["tasks"].pop(reply_id, None)
        if task is None:
            return JSONResponse({"cancelled": False})
        task.cancel()
        replies.close(reply_id)
        return JSONResponse({"cancelled": True})

    routes: list[Route] = [
        Route("/health", health, methods=["GET"]),
        Route("/info", info, methods=["GET"]),
        Route("/prompt", prompt, methods=["POST"]),
        Route("/events/{reply_id}", events, methods=["GET"]),
        Route("/cancel", cancel, methods=["POST"]),
    ]

    builtin = {(r.path, m) for r in routes for m in r.methods}
    for spec in sidecar.routes.routes():
        key = (spec.path, spec.method)
        if key in builtin:
            raise DeclarationError(
                f"插件路由与内置路由冲突:{spec.method} {spec.path}"
                f"(插件 {spec.owner!r})",
            )
        builtin.add(key)
        routes.append(Route(spec.path, _wrap(spec.handler, sidecar.bus,
                                             spec.method, spec.path),
                            methods=[spec.method]))

    app = Starlette(
        routes=routes,
        middleware=[Middleware(_RequestTelemetry, bus=sidecar.bus)],
        debug=False,
    )
    app.state.replies = replies
    return app


def _wrap(handler: Callable[..., Any], bus: Any, method: str,
          path: str) -> Callable[[Request], Any]:
    """包装插件路由处理器:统一异步、宽容返回值、记录异常。

    处理器抛错时返回 500 并发出 ``server/route-error`` —— 不让一个坏路由
    把 traceback 直接喷给调用方。
    """

    async def endpoint(request: Request) -> Response:
        try:
            result = handler(request)
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"
            logger.exception("插件路由 %s %s 抛错", method, path)
            await bus.emit("server/route-error", method=method, path=path,
                           error=detail)
            return JSONResponse({"error": "路由处理器抛错", "detail": detail},
                                status_code=500)
        if isinstance(result, Response):
            return result
        return JSONResponse(result)                    # 宽容:dict 也能返回

    endpoint.__name__ = getattr(handler, "__name__", "plugin_endpoint")
    return endpoint


class _RequestTelemetry:
    """纯 ASGI 中间件:每个 HTTP 请求完成后发 ``server/request``。"""

    def __init__(self, app: Any, bus: Any) -> None:
        self.app = app
        self.bus = bus

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        status = {"code": 0}

        async def send_wrapper(message: dict) -> None:
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            await self.bus.emit(
                "server/request",
                method=scope.get("method", ""),
                path=scope.get("path", ""),
                status=status["code"],
                ms=round((time.perf_counter() - started) * 1000, 2),
            )


def _port_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


async def run(sidecar: "Sidecar") -> int:
    import uvicorn

    host, port = sidecar.options.host, sidecar.options.port
    if not _port_available(host, port):
        print(f"端口 {host}:{port} 已被占用", file=sys.stderr)
        return 3

    app = build_app(sidecar)
    await sidecar.bus.emit("server/listening", mode="http", host=host, port=port)

    config = uvicorn.Config(
        app, host=host, port=port, log_level="warning", access_log=False,
    )
    server = uvicorn.Server(config)
    try:
        await server.serve()
    except asyncio.CancelledError:  # pragma: no cover
        pass
    return 0
