"""目录形态插件:带配套模块与数据文件。"""

from __future__ import annotations

from agentscope.agent import Agent
from agentscope.message import TextBlock
from agentscope.tool import FunctionTool, ToolResponse

from .api_client import describe

CITIES = ("北京", "上海", "广州")


async def get_weather(city: str) -> ToolResponse:
    """查询指定城市的实时天气。

    Args:
        city (str): 城市名,例如「北京」
    """
    return ToolResponse(content=[
        TextBlock(type="text", text=f"{city}:{describe()}"),
    ])


async def list_cities() -> ToolResponse:
    """列出支持的城市。"""
    return ToolResponse(content=[
        TextBlock(type="text", text="、".join(CITIES)),
    ])


async def setup(agent: Agent) -> None:
    await agent.toolkit.add_tool(FunctionTool(get_weather, is_read_only=True))
    await agent.toolkit.add_tool(FunctionTool(list_cities, is_read_only=True))
