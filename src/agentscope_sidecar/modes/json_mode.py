"""``json`` 模式(设计文档 §11.4):事件流 JSONL 到 stdout,最后一行是 ``result``。

**stdout 只有事件流,日志全部走 stderr。**
"""

from __future__ import annotations

import json
import sys
import uuid
from typing import TYPE_CHECKING, Any

from agentscope.message import UserMsg

from ..errors import ConventionError
from ._wire import event_to_wire, message_to_wire, now_iso

if TYPE_CHECKING:  # pragma: no cover
    from ..app import Sidecar


def _write(line: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(line, ensure_ascii=False) + "\n")
    sys.stdout.flush()


async def run(sidecar: "Sidecar") -> int:
    task = sidecar.options.task
    if not task:
        raise ConventionError("json 模式需要一个任务参数")

    agent = sidecar.build_agent()
    reply_id = uuid.uuid4().hex[:12]
    final: Any = None

    try:
        async for event in agent.reply_stream(
            UserMsg(name="user", content=task), yield_final_msg=True,
        ):
            if not hasattr(event, "type"):
                final = event                        # yield_final_msg 的最后一项是 Msg
                continue
            await sidecar.forward_event(event)
            _write(event_to_wire(event, reply_id))
    except Exception as exc:
        _write({"type": "error", "ts": now_iso(),
                "error": f"{type(exc).__name__}: {exc}"})
        return 1

    result: dict[str, Any] = {"type": "result", "ts": now_iso(), "reply_id": reply_id}
    if final is not None:
        result.update(message_to_wire(final))
    _write(result)
    return 0
