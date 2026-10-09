"""与 ``dup_route_a`` 冲突。"""


async def setup(routes):
    routes.add("GET", "/dup", lambda r: None)
