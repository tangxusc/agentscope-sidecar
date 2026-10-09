"""事件与响应的线格式(设计文档 §11.7)。

``json`` 与 ``rpc`` 两种模式共用。**stdout 只能出现这里的结构**,日志走 stderr。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

#: 序列化时丢弃的噪声字段(内部 id、自动生成的时间戳等)。
_DROP = {"id", "created_at", "metadata"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z",
    )


def event_type(event: Any) -> str:
    """线格式的事件名 —— 小写(设计文档 §11.7)。

    AgentScope 的 ``EventType`` 成员名是大写(``REPLY_START``);``event.type``
    可能是枚举成员、也可能是构造时直接给的字符串,两种都要归一。
    """
    raw = getattr(event, "type", "unknown")
    value = getattr(raw, "value", raw)
    text = str(value)
    if text.startswith("EventType."):
        text = text.split(".", 1)[1]
    return text.lower()


def event_to_wire(event: Any, reply_id: str | None = None) -> dict[str, Any]:
    """把一个 AgentScope 事件转成可 JSON 化的字典。"""
    payload: dict[str, Any] = {}
    if hasattr(event, "model_dump"):
        try:
            payload = event.model_dump(mode="json")
        except Exception:  # pragma: no cover - 兜底
            payload = dict(vars(event))
    elif hasattr(event, "__dict__"):
        payload = dict(vars(event))

    for key in _DROP:
        payload.pop(key, None)

    payload["type"] = event_type(event)
    payload["ts"] = now_iso()
    if reply_id is not None:
        payload["reply_id"] = reply_id
    return payload


def message_to_wire(msg: Any) -> dict[str, Any]:
    """把最终 Msg 转成 ``result`` 行的内容。"""
    content: list[dict[str, Any]] = []
    for block in getattr(msg, "content", []) or []:
        if hasattr(block, "model_dump"):
            try:
                content.append(block.model_dump(mode="json"))
                continue
            except Exception:  # pragma: no cover
                pass
        content.append({"type": "unknown", "repr": repr(block)})
    return {"content": content}
