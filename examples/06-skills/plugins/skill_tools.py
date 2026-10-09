"""看 ``SYSTEM.md`` 与 ``skills/`` 有没有真的挂上。

这两个都由**内置插件**读(`system_prompt` / `skills`),和你的插件走同一套机制。
本插件只提供一个工具把拼好的系统提示词打出来。

工具是**闭包** —— 它捕获了 ``agent``,所以能在调用时(所有中间件都挂好之后)
再取提示词。``setup`` 期间取还太早。
"""

from __future__ import annotations

from agentscope.agent import Agent
from agentscope.message import TextBlock
from agentscope.tool import FunctionTool, ToolResponse


async def setup(agent: Agent) -> None:
    async def show_prompt() -> ToolResponse:
        """打印当前的系统提示词。"""
        text = await agent._get_system_prompt()
        return ToolResponse(content=[TextBlock(type="text", text=text)])

    await agent.toolkit.add_tool(FunctionTool(show_prompt, is_read_only=True))
