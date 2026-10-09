"""提供一个服务给别的插件用。"""

from __future__ import annotations

from agentscope_sidecar import Context, Effect


class Catalog:
    """一个简单的服务对象。"""

    def __init__(self) -> None:
        self.items = ["a", "b"]

    def add(self, item: str) -> None:
        self.items.append(item)


async def setup(ctx: Context) -> Effect:
    catalog = Catalog()
    ctx.provide("catalog", catalog)

    def cleanup() -> None:
        catalog.items.clear()

    return cleanup
