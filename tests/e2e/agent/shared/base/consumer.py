"""跨目录消费服务。

``greeting`` 由 ``greeting.py`` 提供,而按插件 id 排序 ``consumer`` 先加载 ——
此刻服务还不存在。注入拿到的是**惰性代理**:写进闭包,等 ``sidecar/ready``
(所有插件都跑完之后)再读,那时它已经就绪。

这就是惰性的用处 —— 加载顺序无关。
"""

from __future__ import annotations

from pathlib import Path

from agentscope_sidecar import Context, Effect

LOG = Path(__file__).parent.parent.parent / "consumer.log"


async def setup(greeting, ctx: Context, events) -> Effect:
    def on_ready(**_: object) -> None:
        LOG.write_text(f"{ctx.id}|{greeting}", encoding="utf-8")

    dispose = events.on("sidecar/ready", on_ready)

    def cleanup() -> None:
        dispose()
        LOG.unlink(missing_ok=True)

    return cleanup
