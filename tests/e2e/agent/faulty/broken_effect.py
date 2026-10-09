"""effect 抛错 —— 不该阻断其余清理。"""


async def setup():
    def cleanup() -> None:
        raise RuntimeError("effect 故意炸了")

    return cleanup
