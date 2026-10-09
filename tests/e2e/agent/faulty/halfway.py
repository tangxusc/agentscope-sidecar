"""注册了一半才炸。

**失败插件留下的东西必须被回滚。** 不然它的状态是 ``failed``,但已经挂上的工具、
路由、命令、服务全都还活着 —— 报告说「什么都没贡献」,实际工具能调、路由能访问。
这比直接失败更糟:没有任何信号。
"""

from __future__ import annotations

from agentscope.agent import Agent
from agentscope.message import TextBlock
from agentscope.tool import FunctionTool, ToolResponse

from agentscope_sidecar import Context


async def ghost_tool() -> ToolResponse:
    """半成品工具。"""
    return ToolResponse(content=[TextBlock(type="text", text="x")])


async def ghost_route(request):
    return {"ghost": True}


def ghost_command(args: str) -> None:
    pass


async def setup(agent: Agent, ctx: Context, routes, commands) -> None:
    await agent.toolkit.add_tool(FunctionTool(ghost_tool, is_read_only=True))
    routes.add("GET", "/ghost", ghost_route)
    commands.add("ghost", ghost_command)
    ctx.provide("ghost_service", 1)
    raise RuntimeError("装到一半炸了")
