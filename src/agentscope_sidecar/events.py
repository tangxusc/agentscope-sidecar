"""事件总线(设计文档 §6.2)。

**只有一种分发语义:广播。** 所有订阅者都被调用,返回值忽略;单个订阅者抛错被
捕获记录,**不影响其他订阅者**。

底座不提供「短路链」式的分发 —— 没有任何一处会发那种事件,留着一个没人发的
机制只会让插件作者订上一个永远不会被调用的回调。
"""

from __future__ import annotations

import inspect
import logging
from collections import defaultdict
from typing import Any, Callable

logger = logging.getLogger("sidecar.events")

Handler = Callable[..., Any]


class EventBus:
    """极简异步事件总线。订阅关系由调用方(插件自己的 effect)负责回收。"""

    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = defaultdict(list)

    def on(self, event: str, handler: Handler) -> Callable[[], None]:
        """订阅事件,返回退订函数。**不是装饰器。**"""
        self._handlers[event].append(handler)

        def dispose() -> None:
            try:
                self._handlers[event].remove(handler)
            except ValueError:
                pass

        return dispose

    def listener_count(self, event: str) -> int:
        return len(self._handlers.get(event, ()))

    def total_listeners(self) -> int:
        return sum(len(v) for v in self._handlers.values())

    async def emit(self, event: str, **payload: Any) -> None:
        """广播。单个订阅者抛错不阻断其余订阅者。

        遍历的是 handler 列表的**副本** —— 订阅者可以在回调里退订自己,那一轮
        的分发不受影响。
        """
        for handler in list(self._handlers.get(event, ())):
            try:
                result = handler(**payload)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                logger.exception("事件 %s 的一个订阅者抛错,已跳过", event)
