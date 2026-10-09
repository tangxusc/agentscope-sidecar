"""一个**没有任何权限规则**的普通工具。

它非只读,所以 ``default`` 模式下需要用户确认;``bypass`` 下直接放行。测试拿它
当对照 —— ``danger`` 的 ``wipe`` 被 deny 规则挡住,``reports`` 的 ``make_report``
被 ask 规则挡住,两个都不能用来看模式差异。
"""

from __future__ import annotations

from pathlib import Path

from agentscope.agent import Agent
from agentscope.message import TextBlock
from agentscope.tool import FunctionTool, ToolResponse

LOG = Path(__file__).parent.parent / "notes.log"


async def write_note(text: str) -> ToolResponse:
    """记一条笔记。

    Args:
        text (str): 笔记内容
    """
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(f"{text}\n")
    return ToolResponse(content=[TextBlock(type="text", text=f"记下了:{text}")])


async def setup(agent: Agent) -> None:
    await agent.toolkit.add_tool(FunctionTool(write_note))
