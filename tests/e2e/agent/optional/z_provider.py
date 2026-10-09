"""给 ``soft_dep`` 提供 store —— 加载顺序在它之后。"""

from __future__ import annotations

from agentscope_sidecar import Context, Effect


async def setup(ctx: Context) -> Effect:
    ctx.provide("store", {"k": 1})

    def cleanup() -> None:
        ctx.remove("store")

    return cleanup
