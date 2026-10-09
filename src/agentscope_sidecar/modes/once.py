"""``once`` 模式(设计文档 §11.2):跑一个任务,文本结果到 stdout,退出。

日志走 stderr,stdout 只有结果文本。
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from agentscope.event import EventType
from agentscope.message import UserMsg

from ..errors import ConventionError

if TYPE_CHECKING:  # pragma: no cover
    from ..app import Sidecar


async def run(sidecar: "Sidecar") -> int:
    task = sidecar.options.task
    if not task:
        raise ConventionError("once 模式需要一个任务参数")

    agent = sidecar.build_agent()

    async for event in agent.reply_stream(UserMsg(name="user", content=task)):
        await sidecar.forward_event(event)
        if event.type is EventType.TEXT_BLOCK_DELTA:
            sys.stdout.write(event.delta)
            sys.stdout.flush()
        elif event.type is EventType.TOOL_CALL_START:
            print(f"\n[tool] {event.tool_call_name}", file=sys.stderr, flush=True)
    sys.stdout.write("\n")
    sys.stdout.flush()
    return 0
