"""遥测服务(设计文档 §6.5)。订阅总线,累计计数并保留时间线。"""

from __future__ import annotations

import json
import logging
import time
from collections import Counter
from pathlib import Path
from typing import Any

from ..events import EventBus

logger = logging.getLogger("sidecar.telemetry")

#: 底座会发出的生命周期事件。**只列真的会发的** —— 订阅一个永不触发的名字,
#: 计数永远是 0,看着像功能没生效。
LIFECYCLE_EVENTS = (
    "sidecar/boot",
    "sidecar/plugin-loaded",
    "sidecar/plugin-shadowed",
    "sidecar/plugin-failed",
    "sidecar/plugin-unloaded",
    "sidecar/ready",
    "sidecar/reloaded",
    "sidecar/shutdown",
)

#: agent 循环事件的统一入口。``type`` 字段区分具体事件(见设计文档 §6.6)。
AGENT_EVENT = "agent/event"


class Telemetry:
    """事件收集器。

    ``timeline`` 是**有界**的:常驻服务跑几天不清理就是内存泄漏。计数不受
    影响 —— 计数是无界的精确值,时间线只是最近的样本。
    """

    def __init__(self, sink: Path | None = None, *, timeline_limit: int = 1000) -> None:
        self.counters: Counter[str] = Counter()
        self.timeline: list[dict[str, Any]] = []
        self.timeline_limit = timeline_limit
        self._sink = sink
        self._file = None
        self._started = time.time()

    def attach(self, bus: EventBus) -> None:
        for event in (*LIFECYCLE_EVENTS, AGENT_EVENT):
            bus.on(event, self._handler_for(event))
        if self._sink is not None:
            self._sink.parent.mkdir(parents=True, exist_ok=True)
            self._file = self._sink.open("a", encoding="utf-8")

    def _handler_for(self, event: str):
        def handler(**payload: Any) -> None:
            self.record(event, payload)

        return handler

    def record(self, event: str, payload: dict[str, Any]) -> None:
        self.counters[event] += 1
        entry = {
            "ts": round(time.time() - self._started, 4),
            "event": event,
            "payload": _jsonable(payload),
        }
        self.timeline.append(entry)
        if len(self.timeline) > self.timeline_limit:
            del self.timeline[: len(self.timeline) - self.timeline_limit]
        if self._file is not None:
            self._file.write(json.dumps(entry, ensure_ascii=False) + "\n")
            self._file.flush()

    def snapshot(self) -> dict[str, Any]:
        return {
            "elapsed": round(time.time() - self._started, 4),
            "counters": dict(self.counters),
            "timeline_size": len(self.timeline),
        }

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)
