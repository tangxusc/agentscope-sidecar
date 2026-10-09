"""``console`` 模式(设计文档 §11.3):长驻 REPL,唯一的交互模式。"""

from __future__ import annotations

import asyncio
import sys
from typing import TYPE_CHECKING

from agentscope.event import EventType
from agentscope.message import UserMsg

if TYPE_CHECKING:  # pragma: no cover
    from ..app import Sidecar


async def run(sidecar: "Sidecar") -> int:
    agent = sidecar.build_agent()
    loop = asyncio.get_running_loop()
    print("输入任务,空行退出。", file=sys.stderr)

    while True:
        try:
            line = await loop.run_in_executor(None, input, "> ")
        except (EOFError, KeyboardInterrupt):
            break
        if not line.strip():
            break

        async for event in agent.reply_stream(
            UserMsg(name="user", content=line.strip()),
        ):
            await sidecar.forward_event(event)
            if event.type is EventType.TEXT_BLOCK_DELTA:
                sys.stdout.write(event.delta)
                sys.stdout.flush()
        sys.stdout.write("\n")
        sys.stdout.flush()

    return 0
