"""Effect 的执行语义(设计文档 §4.3)。"""

from __future__ import annotations

import asyncio

import pytest

from agentscope_sidecar.effects import run_effect


def test_sync_effect() -> None:
    seen: list[str] = []
    asyncio.run(run_effect(lambda: seen.append("x")))
    assert seen == ["x"]


def test_async_effect() -> None:
    seen: list[str] = []

    async def cleanup() -> None:
        seen.append("async")

    asyncio.run(run_effect(cleanup))
    assert seen == ["async"]


def test_effect_returning_none() -> None:
    asyncio.run(run_effect(lambda: None))       # 不该抛


def test_failing_effect_is_swallowed(caplog) -> None:
    import logging

    def boom() -> None:
        raise ValueError("清理炸了")

    with caplog.at_level(logging.ERROR, logger="sidecar.effects"):
        asyncio.run(run_effect(boom, label="alpha 的 effect"))
    assert "alpha 的 effect" in caplog.text


def test_failing_async_effect_is_swallowed(caplog) -> None:
    import logging

    async def boom() -> None:
        raise ValueError("异步清理炸了")

    with caplog.at_level(logging.ERROR, logger="sidecar.effects"):
        asyncio.run(run_effect(boom, label="beta 的 effect"))
    assert "beta 的 effect" in caplog.text


def test_effect_taking_no_arguments() -> None:
    """effect 必须无参数 —— 传参会在调用点炸,这里确认签名约定。"""
    calls: list[int] = []
    asyncio.run(run_effect(lambda: calls.append(1)))
    assert calls == [1]
