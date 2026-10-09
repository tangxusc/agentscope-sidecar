"""Effect:``setup`` 的返回值(设计文档 §4.3)。

``setup`` 返回一个清理函数,底座在退出时逆序调用。同步或异步都可以 ——
``agent.toolkit.remove_tool`` 是异步的,禁了异步就没法摘工具。
"""

from __future__ import annotations

import inspect
import logging
from typing import Awaitable, Callable

logger = logging.getLogger("sidecar.effects")

#: 清理函数。无参数、无返回值。同步或异步皆可。
Effect = Callable[[], "None | Awaitable[None]"]


async def run_effect(effect: Effect, *, label: str = "effect") -> None:
    """调用一个 effect。抛错只记录,不向上传播。

    单个 effect 失败不该阻断其余清理 —— 否则一个插件的问题会让整个底座
    关不干净。
    """
    try:
        result = effect()
        if inspect.isawaitable(result):
            await result
    except Exception:
        logger.exception("执行 %s 时出错,已跳过", label)


__all__ = ["Effect", "run_effect"]
