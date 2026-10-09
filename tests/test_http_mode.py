"""HTTP 模式的内部结构(设计文档 §11.6)。

端到端在 ``tests/e2e/test_http.py``;这里测只能从内部观察的部分:事件队列、
路由包装、路由冲突、启动流程。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from starlette.responses import JSONResponse

from agentscope_sidecar.errors import DeclarationError
from agentscope_sidecar.modes import http_mode
from agentscope_sidecar.modes.http_mode import _Replies, _port_available, _wrap
from agentscope_sidecar.events import EventBus

from helpers import Project


# ------------------------------------------------------------------ _Replies


def test_replies_open_push_stream_close() -> None:
    async def main() -> list[str]:
        replies = _Replies()
        reply_id = replies.open()
        replies.push(reply_id, {"a": 1})
        replies.push(reply_id, {"b": 2})
        replies.close(reply_id)
        return [
            chunk.decode() async for chunk in replies.stream(reply_id)
        ]

    chunks = asyncio.run(main())
    assert len(chunks) == 2
    assert json.loads(chunks[0].removeprefix("data: ").strip()) == {"a": 1}


def test_replies_stream_is_recycled_after_end() -> None:
    async def main() -> None:
        replies = _Replies()
        reply_id = replies.open()
        replies.close(reply_id)
        async for _ in replies.stream(reply_id):
            pass
        # 队列已回收 —— 再订阅就是未知 id
        second = [c.decode() async for c in replies.stream(reply_id)]
        assert "unknown reply_id" in second[0]

    asyncio.run(main())


def test_replies_push_to_unknown_id_is_ignored() -> None:
    replies = _Replies()
    replies.push("nope", {"a": 1})                 # 不该抛
    replies.close("nope")
    replies.discard("nope")


def test_replies_unknown_stream_yields_error_frame() -> None:
    async def main() -> str:
        chunks = [c.decode() async for c in _Replies().stream("nope")]
        return chunks[0]

    assert "unknown reply_id" in asyncio.run(main())


# ------------------------------------------------------------------ _wrap


def test_wrap_passes_response_through() -> None:
    async def handler(request):
        return JSONResponse({"ok": True})

    async def main() -> object:
        return await _wrap(handler, EventBus(), "GET", "/x")(None)

    response = asyncio.run(main())
    assert response.status_code == 200


def test_wrap_accepts_sync_handler() -> None:
    def handler(request):
        return {"sync": True}

    async def main() -> object:
        return await _wrap(handler, EventBus(), "GET", "/x")(None)

    response = asyncio.run(main())
    assert json.loads(response.body) == {"sync": True}


def test_wrap_accepts_plain_dict_return() -> None:
    async def handler(request):
        return {"plain": 1}

    async def main() -> object:
        return await _wrap(handler, EventBus(), "GET", "/x")(None)

    response = asyncio.run(main())
    assert json.loads(response.body) == {"plain": 1}


def test_wrap_turns_exception_into_500_and_event() -> None:
    async def handler(request):
        raise RuntimeError("路由炸了")

    bus = EventBus()
    seen: list[dict] = []
    bus.on("server/route-error", lambda **kw: seen.append(kw))

    async def main() -> object:
        return await _wrap(handler, bus, "GET", "/boom")(None)

    response = asyncio.run(main())
    assert response.status_code == 500
    body = json.loads(response.body)
    assert "RuntimeError" in body["detail"]
    assert seen == [{"method": "GET", "path": "/boom",
                     "error": "RuntimeError: 路由炸了"}]


def test_wrap_keeps_handler_name() -> None:
    async def my_handler(request):
        return {}

    assert _wrap(my_handler, EventBus(), "GET", "/x").__name__ == "my_handler"


def test_wrap_anonymous_handler_gets_a_default_name() -> None:
    endpoint = _wrap(lambda request: {}, EventBus(), "GET", "/x")
    assert endpoint.__name__ == "<lambda>"

    class _Callable:
        def __call__(self, request):
            return {}

    assert _wrap(_Callable(), EventBus(), "GET", "/x").__name__ == (
        "plugin_endpoint"
    )


# ------------------------------------------------------------------ build_app


def test_build_app_registers_builtin_and_plugin_routes(project: Project) -> None:
    project.plugin("web", '''
        from starlette.responses import JSONResponse


        async def page(request):
            return JSONResponse({"page": True})


        async def setup(routes) -> None:
            routes.add("GET", "/page", page)
    ''')
    sidecar = project.boot()
    try:
        app = http_mode.build_app(sidecar)
        paths = {route.path for route in app.routes}
        assert {"/health", "/info", "/prompt", "/cancel", "/page"} <= paths
        assert "/events/{reply_id}" in paths
    finally:
        sidecar.shutdown()


def test_build_app_rejects_conflict_with_builtin(project: Project) -> None:
    """插件路由撞上内置路由 —— 启动即失败,不静默覆盖。"""
    project.plugin("hijack", '''
        async def fake(request):
            return {}


        async def setup(routes) -> None:
            routes.add("GET", "/health", fake)
    ''')
    sidecar = project.boot()
    try:
        with pytest.raises(DeclarationError, match="与内置路由冲突"):
            http_mode.build_app(sidecar)
    finally:
        sidecar.shutdown()


# ------------------------------------------------------------------ run


def test_run_returns_zero_after_server_stops(
    project: Project, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import uvicorn

    async def fake_serve(self) -> None:
        return None

    monkeypatch.setattr(uvicorn.Server, "serve", fake_serve)

    sidecar = project.boot(mode="http")
    try:
        assert asyncio.run(http_mode.run(sidecar)) == 0
    finally:
        sidecar.shutdown()


def test_run_returns_three_when_port_busy(
    project: Project, capsys: pytest.CaptureFixture,
) -> None:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        port = sock.getsockname()[1]

        sidecar = project.boot(mode="http", port=port, host="127.0.0.1")
        try:
            assert asyncio.run(http_mode.run(sidecar)) == 3
        finally:
            sidecar.shutdown()

    assert "已被占用" in capsys.readouterr().err


def test_port_available_reports_false_when_taken() -> None:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        port = sock.getsockname()[1]
        assert _port_available("127.0.0.1", port) is False


def test_port_available_reports_true_for_free_port() -> None:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    assert _port_available("127.0.0.1", port) is True


def test_build_app_attaches_replies(project: Project) -> None:
    sidecar = project.boot(mode="http")
    try:
        app = http_mode.build_app(sidecar)
        assert isinstance(app.state.replies, _Replies)
    finally:
        sidecar.shutdown()


def test_request_telemetry_emits_event(project: Project) -> None:
    """每个请求完成后发 ``server/request`` —— 含状态码与耗时。"""
    sidecar = project.boot(mode="http")
    try:
        bus = sidecar.bus
        seen: list[dict] = []
        bus.on("server/request", lambda **kw: seen.append(kw))

        middleware = http_mode._RequestTelemetry(_noop_app, bus)

        async def main() -> None:
            await middleware(
                {"type": "http", "method": "GET", "path": "/x"},
                None, _noop_send,
            )

        asyncio.run(main())
        assert seen and seen[0]["path"] == "/x"
        assert seen[0]["status"] == 200
        assert "ms" in seen[0]
    finally:
        sidecar.shutdown()


def test_request_telemetry_ignores_non_http_scopes() -> None:
    """WebSocket / lifespan 直接透传,不发 HTTP 事件。"""
    seen: list[dict] = []
    bus = EventBus()
    bus.on("server/request", lambda **kw: seen.append(kw))

    calls: list[str] = []

    async def app(scope, receive, send) -> None:
        calls.append(scope["type"])

    middleware = http_mode._RequestTelemetry(app, bus)
    asyncio.run(middleware({"type": "lifespan"}, None, None))
    assert calls == ["lifespan"]
    assert seen == []


async def _noop_app(scope, receive, send) -> None:
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b""})


async def _noop_send(message: dict) -> None:
    return None


def test_module_has_no_stray_paths() -> None:
    """``_wire`` 与 ``http_mode`` 是同级模块 —— 别再写第二份线格式。"""
    source = Path(http_mode.__file__).read_text(encoding="utf-8")
    assert "def event_to_wire" not in source
    assert "from ._wire import" in source
