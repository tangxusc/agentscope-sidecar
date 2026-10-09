"""命令 + 工具 + 自带清理。"""

from __future__ import annotations

from pathlib import Path

from agentscope.agent import Agent
from agentscope.message import TextBlock
from agentscope.tool import FunctionTool, ToolResponse

from agentscope_sidecar import Effect

LOG = Path(__file__).parent.parent / "reports.log"


async def make_report(topic: str) -> ToolResponse:
    """生成一份报告。

    Args:
        topic (str): 主题
    """
    LOG.write_text(f"report:{topic}", encoding="utf-8")
    return ToolResponse(content=[TextBlock(type="text", text=f"已生成 {topic}")])


def on_report(args: str) -> None:
    LOG.write_text(f"command:{args}", encoding="utf-8")


async def setup(agent: Agent, commands) -> Effect:
    await agent.toolkit.add_tool(FunctionTool(make_report, is_read_only=True))
    commands.add("report", on_report)

    def cleanup() -> None:
        commands.remove("report")
        if LOG.is_file():
            LOG.unlink()

    return cleanup
