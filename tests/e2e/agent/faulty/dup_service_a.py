"""两个插件注册同名服务。"""

from agentscope_sidecar import Context


async def setup(ctx: Context):
    ctx.provide("shared_name", 1)
