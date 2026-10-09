"""消费 ``catalog`` 服务。

``catalog`` 由 ``catalog_service`` 提供,按插件 id 排序这个文件先加载 ——
此刻服务还不存在,注入拿到的是**惰性代理**。写进闭包,等真正调用工具时再解析。

这就是惰性的用处:加载顺序无关。
"""

from __future__ import annotations

from agentscope.agent import Agent
from agentscope.message import TextBlock
from agentscope.tool import FunctionTool, ToolResponse


async def list_items() -> ToolResponse:
    """列出目录里的条目。"""
    return ToolResponse(content=[
        TextBlock(type="text", text=",".join(CATALOG.items)),
    ])


CATALOG = None


async def setup(agent: Agent, catalog) -> None:
    global CATALOG
    CATALOG = catalog
    await agent.toolkit.add_tool(FunctionTool(list_items, is_read_only=True))
