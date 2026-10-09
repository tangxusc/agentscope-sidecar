"""消费 ``catalog`` 服务 —— 按名称注入。"""

from __future__ import annotations

from agentscope.agent import Agent
from agentscope.message import TextBlock
from agentscope.tool import FunctionTool, ToolResponse


async def list_catalog() -> ToolResponse:
    """列出目录里的条目。"""
    return ToolResponse(content=[
        TextBlock(type="text", text=",".join(CATALOG.items)),
    ])


CATALOG = None


async def setup(agent: Agent, catalog) -> None:
    global CATALOG
    CATALOG = catalog
    await agent.toolkit.add_tool(FunctionTool(list_catalog, is_read_only=True))
