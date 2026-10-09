"""慢工具 —— 用来测并发与超时。"""

from __future__ import annotations

import asyncio

from agentscope.agent import Agent
from agentscope.message import TextBlock
from agentscope.tool import FunctionTool, ToolResponse


async def wait(seconds: float = 0.05) -> ToolResponse:
    """等一会儿。

    Args:
        seconds (float): 秒数
    """
    await asyncio.sleep(seconds)
    return ToolResponse(content=[TextBlock(type="text", text="等完了")])


async def setup(agent: Agent) -> None:
    await agent.toolkit.add_tool(FunctionTool(wait, is_read_only=True))
