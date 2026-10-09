"""可选依赖:参数给默认值,用 ``ctx.get`` 判定。"""

from __future__ import annotations

from pathlib import Path

from agentscope_sidecar import Context, Effect

LOG = Path(__file__).parent.parent / "soft.log"


async def setup(ctx: Context, store=None) -> Effect:
    resolved = ctx.get("store")
    LOG.write_text("有 store" if resolved is not None else "没有 store",
                   encoding="utf-8")

    def cleanup() -> None:
        LOG.unlink(missing_ok=True)

    return cleanup
