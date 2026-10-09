"""路由路径不合法。"""


async def setup(routes):
    routes.add("GET", "no-slash", lambda r: None)
