"""``rpc`` 模式(设计文档 §11.5):JSON-RPC 2.0 over stdio,换行分隔。

**stdout 只发协议帧,日志走 stderr。**

请求:``initialize`` / ``prompt`` / ``cancel`` / ``ping`` / ``shutdown``
通知:``event`` / ``plugin`` / ``log``
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from typing import TYPE_CHECKING, Any

from agentscope.message import UserMsg

from ._wire import event_to_wire, message_to_wire

if TYPE_CHECKING:  # pragma: no cover
    from ..app import Sidecar

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
BUSY = -32001


class _Session:
    """一次 ``prompt`` 的运行状态。"""

    def __init__(self, reply_id: str, task: asyncio.Task) -> None:
        self.reply_id = reply_id
        self.task = task
        self.cancelled = False


async def run(sidecar: "Sidecar") -> int:
    loop = asyncio.get_running_loop()
    sessions: dict[str, _Session] = {}
    active_session: str | None = None
    stopping = False

    # 写锁必须是**本次运行**的局部对象。做成模块级全局会在第二次 run 时复用
    # 已绑定到上一个事件循环的锁,asyncio 会直接抛 "bound to a different loop"。
    write_lock = asyncio.Lock()

    async def _send(payload: dict[str, Any]) -> None:
        async with write_lock:
            sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
            sys.stdout.flush()

    async def _notify(method: str, params: dict[str, Any]) -> None:
        await _send({"jsonrpc": "2.0", "method": method, "params": params})

    async def _result(req_id: Any, result: Any) -> None:
        await _send({"jsonrpc": "2.0", "id": req_id, "result": result})

    async def _error(req_id: Any, code: int, message: str) -> None:
        await _send({"jsonrpc": "2.0", "id": req_id,
                     "error": {"code": code, "message": message}})

    def emit_plugin_event(**payload: Any) -> None:
        asyncio.ensure_future(_notify("plugin", payload))

    # 只订阅真的会发出的事件 —— 订阅一个永不触发的名字,客户端永远收不到通知,
    # 而且没有任何提示。列表见设计文档 §6.6。
    for event in (
        "sidecar/plugin-loaded", "sidecar/plugin-failed",
        "sidecar/plugin-shadowed", "sidecar/plugin-unloaded",
    ):
        sidecar.bus.on(event, emit_plugin_event)

    async def run_prompt(reply_id: str, text: str) -> None:
        nonlocal active_session
        try:
            agent = sidecar.build_agent()
            async for event in agent.reply_stream(
                UserMsg(name="user", content=text), yield_final_msg=True,
            ):
                if sessions.get(reply_id) is None:
                    return                            # 已被取消并清理
                if not hasattr(event, "type"):
                    await _notify("event", {
                        "reply_id": reply_id, "type": "result",
                        **message_to_wire(event),
                    })
                    continue
                await sidecar.forward_event(event)
                await _notify("event", event_to_wire(event, reply_id))
        except asyncio.CancelledError:
            await _notify("event", {"reply_id": reply_id, "type": "cancelled"})
            raise
        except Exception as exc:
            await _notify("event", {
                "reply_id": reply_id, "type": "error",
                "error": f"{type(exc).__name__}: {exc}",
            })
        finally:
            sessions.pop(reply_id, None)
            if active_session == reply_id:
                active_session = None

    await sidecar.bus.emit("rpc/connected")
    try:
        while not stopping:
            try:
                line = await loop.run_in_executor(None, sys.stdin.readline)
            except (EOFError, KeyboardInterrupt):
                break
            if not line:
                break                                 # stdin 关闭 = shutdown
            line = line.strip()
            if not line:
                continue

            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                await _error(None, PARSE_ERROR, "无法解析 JSON")
                continue

            if not isinstance(request, dict):
                await _error(None, INVALID_REQUEST, "请求必须是对象")
                continue

            req_id = request.get("id")
            method = request.get("method")
            params = request.get("params") or {}

            if method == "ping":
                await _result(req_id, {"pong": True})

            elif method == "initialize":
                await _result(req_id, {
                    "model": sidecar.model_ref,
                    "mode": sidecar.options.mode,
                    "permission": sidecar.options.permission,
                    "tools": sidecar.report()["tools"],
                    "middlewares": sidecar.report()["middlewares"],
                    "plugins": [
                        r.id for r in sidecar.loader.records.values()
                        if r.state.value == "loaded"
                    ],
                    "routes": sidecar.routes.describe(),
                })

            elif method == "prompt":
                text = params.get("text")
                if not isinstance(text, str) or not text:
                    await _error(req_id, INVALID_PARAMS,
                                 "params.text 必须是非空字符串")
                    continue
                if active_session is not None:
                    await _error(req_id, BUSY, "同一 session 不允许并发 prompt")
                    continue

                reply_id = uuid.uuid4().hex[:12]
                task = asyncio.ensure_future(run_prompt(reply_id, text))
                sessions[reply_id] = _Session(reply_id, task)
                active_session = reply_id
                await _result(req_id, {"reply_id": reply_id})

            elif method == "cancel":
                reply_id = params.get("reply_id")
                session = sessions.pop(reply_id, None) if reply_id else None
                if session is None:
                    await _result(req_id, {"cancelled": False})
                else:
                    session.task.cancel()
                    await _result(req_id, {"cancelled": True})

            elif method == "shutdown":
                await _result(req_id, {})
                stopping = True

            else:
                await _error(req_id, METHOD_NOT_FOUND, f"未知方法:{method!r}")
    finally:
        for session in sessions.values():
            session.task.cancel()
        if sessions:
            await asyncio.gather(
                *(s.task for s in sessions.values()), return_exceptions=True,
            )
        await sidecar.bus.emit("rpc/disconnected")
    return 0
