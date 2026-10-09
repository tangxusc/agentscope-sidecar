"""事件总线(设计文档 §6.2)。

广播语义:所有订阅者都被调用,单个抛错不阻断其余。``on`` 返回退订函数,不是
装饰器 —— 退订后不再收到事件,重复退订不报错。
"""

from __future__ import annotations

import pytest

from agentscope_sidecar.events import EventBus


# ------------------------------------------------------------------ 订阅


def test_on_returns_disposer() -> None:
    bus = EventBus()
    calls: list[int] = []

    def handler(**_: object) -> None:
        calls.append(1)

    dispose = bus.on("x", handler)
    assert bus.listener_count("x") == 1

    import asyncio
    asyncio.run(bus.emit("x"))
    assert calls == [1]

    dispose()
    assert bus.listener_count("x") == 0


def test_dispose_is_idempotent() -> None:
    bus = EventBus()

    def handler(**_: object) -> None:
        pass

    dispose = bus.on("x", handler)
    dispose()
    dispose()                                    # 不该抛
    assert bus.listener_count("x") == 0


def test_dispose_after_manual_clear_does_not_raise() -> None:
    """订阅者被别处清掉后,再调 dispose 也不能炸。"""
    bus = EventBus()

    def handler(**_: object) -> None:
        pass

    dispose = bus.on("x", handler)
    bus._handlers["x"].clear()
    dispose()
    assert bus.listener_count("x") == 0


def test_listener_count_and_total() -> None:
    bus = EventBus()
    for _ in range(3):
        bus.on("a", lambda **_: None)
    bus.on("b", lambda **_: None)
    assert bus.listener_count("a") == 3
    assert bus.listener_count("nope") == 0
    assert bus.total_listeners() == 4


def test_no_subscribers_is_a_noop() -> None:
    import asyncio

    asyncio.run(EventBus().emit("nothing"))


# ------------------------------------------------------------------ emit


def test_emit_passes_payload() -> None:
    import asyncio

    bus = EventBus()
    seen: list[dict] = []
    bus.on("x", lambda **kw: seen.append(kw))
    asyncio.run(bus.emit("x", a=1, b="二"))
    assert seen == [{"a": 1, "b": "二"}]


def test_emit_awaits_async_handler() -> None:
    import asyncio

    bus = EventBus()
    seen: list[str] = []

    async def handler(**_: object) -> None:
        await asyncio.sleep(0)
        seen.append("async")

    bus.on("x", handler)
    asyncio.run(bus.emit("x"))
    assert seen == ["async"]


def test_emit_handler_error_does_not_block_others() -> None:
    import asyncio

    bus = EventBus()
    seen: list[str] = []

    def boom(**_: object) -> None:
        raise RuntimeError("炸了")

    async def bad(**_: object) -> None:
        raise RuntimeError("也炸了")

    bus.on("x", boom)
    bus.on("x", bad)
    bus.on("x", lambda **_: seen.append("ok"))

    asyncio.run(bus.emit("x"))                   # 不该抛
    assert seen == ["ok"]


def test_emit_uses_a_copy_of_the_handler_list() -> None:
    """订阅者在回调里退订,不影响这一轮的分发。"""
    import asyncio

    bus = EventBus()
    seen: list[str] = []

    def second(**_: object) -> None:
        seen.append("second")

    dispose = bus.on("x", second)

    def first(**_: object) -> None:
        seen.append("first")
        dispose()

    bus.on("x", first)
    asyncio.run(bus.emit("x"))
    assert seen == ["second", "first"]          # 本轮两个都被调用
    assert bus.listener_count("x") == 1          # 只有 second 被退订

    seen.clear()
    asyncio.run(bus.emit("x"))
    assert seen == ["first"]
