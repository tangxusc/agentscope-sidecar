"""两条插件注册同一个路由 —— 后者报错。"""


async def setup(routes):
    routes.add("GET", "/dup", lambda r: None)
