"""与 ``dup_service_a`` 抢同一个服务名。"""

from agentscope_sidecar import Context


async def setup(ctx: Context):
    ctx.provide("shared_name", 2)
