"""只有高优先级目录里才有的插件。"""

from __future__ import annotations

from agentscope.agent import Agent
from agentscope.message import TextBlock
from agentscope.tool import FunctionTool, ToolResponse


async def biz_tool() -> ToolResponse:
    """只在高优先级目录里存在的工具。"""
    return ToolResponse(content=[TextBlock(type="text", text="biz")])


async def setup(agent: Agent) -> None:
    await agent.toolkit.add_tool(FunctionTool(biz_tool, is_read_only=True))
