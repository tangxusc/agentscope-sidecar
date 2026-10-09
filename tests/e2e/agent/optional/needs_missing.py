"""依赖一个永远不存在的服务 —— 拿到惰性代理,记进 unresolved。"""

from __future__ import annotations

from agentscope_sidecar import Context, Effect

LOG = ""


async def setup(never_provided, ctx: Context) -> Effect:
    global LOG
    LOG = repr(never_provided)

    def cleanup() -> None:
        pass

    return cleanup
