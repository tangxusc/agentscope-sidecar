"""需要确认的工具。

``wipe`` **不是只读工具**,所以在 ``default`` 权限模式下需要用户确认。它在被真正
执行时往 ``wipe.log`` 追加一行 —— 测试靠这个文件判断工具到底跑没跑。

只看 ``tool_result`` 是判断不出来的:被拒绝的调用同样会产生 tool result。
"""

from __future__ import annotations

from pathlib import Path

from agentscope.agent import Agent
from agentscope.message import TextBlock
from agentscope.tool import FunctionTool, ToolResponse

LOG = Path(__file__).parent.parent / "wipe.log"


async def wipe(path: str) -> ToolResponse:
    """删除一个路径(危险操作)。

    Args:
        path (str): 要删除的路径
    """
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(f"{path}\n")
    return ToolResponse(content=[TextBlock(type="text", text=f"删除 {path}")])


async def setup(agent: Agent) -> None:
    await agent.toolkit.add_tool(FunctionTool(wipe))
