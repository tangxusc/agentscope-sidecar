"""事件订阅 + 自带清理。"""

from __future__ import annotations

from pathlib import Path

from agentscope_sidecar import Context, Effect

LOG = Path(__file__).parent.parent / "lifecycle.log"

EVENTS = (
    "sidecar/boot",
    "sidecar/plugin-loaded",
    "sidecar/plugin-shadowed",
    "sidecar/plugin-failed",
    "sidecar/ready",
    "sidecar/reloaded",
    "sidecar/shutdown",
    "server/listening",
    "server/request",
    "server/route-error",
    "agent/event",
)


def _record(event: str) -> None:
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(f"{event}\n")


def make_handler(event: str):
    def handler(**payload: object) -> None:
        _record(event)

    return handler


async def setup(events, ctx: Context) -> Effect:
    for event in EVENTS:
        events.on(event, make_handler(event))

    ctx.logger.info("lifecycle 已订阅 %d 个事件", len(EVENTS))

    def cleanup() -> None:
        if LOG.is_file():
            LOG.unlink()

    return cleanup
