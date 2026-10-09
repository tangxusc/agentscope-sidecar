"""低优先级目录:提供一个服务。"""

from __future__ import annotations

from agentscope_sidecar import Context, Effect


async def setup(ctx: Context) -> Effect:
    ctx.provide("greeting", "来自 base 的问候")

    def cleanup() -> None:
        ctx.remove("greeting")

    return cleanup
