"""最小插件:一个工具 + 一个 effect。

跑::

    cd examples/01-hello && sidecar "跟世界打个招呼"
"""

from __future__ import annotations

from pathlib import Path

from agentscope.agent import Agent
from agentscope.message import TextBlock
from agentscope.tool import FunctionTool, ToolResponse

from agentscope_sidecar import Effect

LOG = Path(__file__).parent.parent / "calls.log"


async def greet(name: str) -> ToolResponse:
    """向某人问好。

    Args:
        name (str): 名字
    """
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(f"greet:{name}\n")
    return ToolResponse(content=[TextBlock(type="text", text=f"你好,{name}!")])


async def setup(agent: Agent) -> Effect:
    # 只读工具在 default 权限模式下不需要确认 —— 非交互模式也能直接跑
    await agent.toolkit.add_tool(FunctionTool(greet, is_read_only=True))

    def cleanup() -> None:
        LOG.unlink(missing_ok=True)

    return cleanup
