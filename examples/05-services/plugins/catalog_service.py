"""提供一个服务给别的插件用。

服务是**扁平命名空间**:名字就是契约,没有类型注册、没有接口声明。
插件之间共享代码用服务,不用 import。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agentscope_sidecar import Context, Effect


@dataclass
class Catalog:
    items: list[str] = field(default_factory=lambda: ["apple", "banana"])

    def add(self, item: str) -> None:
        self.items.append(item)


async def setup(ctx: Context) -> Effect:
    catalog = Catalog()
    ctx.provide("catalog", catalog)

    def cleanup() -> None:
        ctx.remove("catalog")

    return cleanup
