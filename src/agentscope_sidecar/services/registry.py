"""路由与命令注册表(设计文档 §6.3 / §6.4)。

这两样**不在 Agent 上**,没法靠快照差分归属 —— 注册表在注册时读
:func:`~agentscope_sidecar.context.current_owner`。加载器在调用 ``setup``
期间设置了它,所以插件作者不用传任何东西。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ..context import current_owner
from ..errors import DeclarationError

#: 路由处理器的签名:``(request) -> Response``,可 async。
Handler = Callable[..., Any]


@dataclass
class RouteSpec:
    method: str
    path: str
    handler: Handler
    owner: str


class RouteRegistry:
    """HTTP 路由注册表。

    只在 ``http`` 模式下挂载;其他模式注册不报错,但不会被使用。
    """

    METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")

    def __init__(self) -> None:
        self._routes: list[RouteSpec] = []

    def add(
        self, method: str, path: str, handler: Handler,
        *, name: str | None = None,
    ) -> RouteSpec:
        """注册路由。``path`` 是完整路径,底座不加前缀。

        ``name`` 只用于报告,不影响匹配 —— 路由由 ``method + path`` 唯一确定。
        """
        method = method.upper()
        if method not in self.METHODS:
            raise DeclarationError(f"HTTP 方法非法:{method!r}")
        if not path.startswith("/"):
            raise DeclarationError(f"路由路径必须以 / 开头:{path!r}")

        owner = current_owner()
        for existing in self._routes:
            if existing.method == method and existing.path == path:
                raise DeclarationError(
                    f"路由冲突:{method} {path} 已被 {existing.owner!r} 注册,"
                    f"{owner!r} 不能重复注册",
                )

        spec = RouteSpec(method=method, path=path, handler=handler, owner=owner)
        self._routes.append(spec)
        return spec

    def remove(self, method: str, path: str) -> None:
        """按 ``method + path`` 移除。不存在时静默返回。"""
        method = method.upper()
        self._routes = [
            r for r in self._routes
            if not (r.method == method and r.path == path)
        ]

    def remove_owned(self, owner: str) -> list[str]:
        """移除某个归属注册的全部路由。返回被移除的 ``"METHOD path"``。

        ``setup`` 半途抛异常时加载器靠它回滚 —— 没有这层,失败插件留下的路由
        会一直挂着,而报告里它已经是 ``failed``。
        """
        removed = [f"{r.method} {r.path}" for r in self._routes if r.owner == owner]
        self._routes = [r for r in self._routes if r.owner != owner]
        return removed

    def routes(self) -> list[RouteSpec]:
        return list(self._routes)

    def describe(self) -> dict[str, str]:
        return {f"{r.method} {r.path}": r.owner for r in self._routes}


class CommandRegistry:
    """CLI 子命令注册表。

    和 :class:`RouteRegistry` 一样记归属 —— 加载器要能按插件回滚,报告里也要
    能看出「这条命令是谁加的」。
    """

    def __init__(self) -> None:
        self._commands: dict[str, Handler] = {}
        self._owners: dict[str, str] = {}

    def add(self, name: str, handler: Handler) -> None:
        if name in self._commands:
            existing = self._owners.get(name, "?")
            raise DeclarationError(
                f"命令 {name!r} 已被 {existing!r} 注册,"
                f"{current_owner()!r} 不能重复注册",
            )
        self._commands[name] = handler
        self._owners[name] = current_owner()

    def remove(self, name: str) -> None:
        self._commands.pop(name, None)
        self._owners.pop(name, None)

    def remove_owned(self, owner: str) -> list[str]:
        """移除某个归属注册的全部命令。"""
        removed = [n for n, o in self._owners.items() if o == owner]
        for name in removed:
            self.remove(name)
        return removed

    def get(self, name: str) -> Handler | None:
        return self._commands.get(name)

    def names(self) -> list[str]:
        return sorted(self._commands)

    def describe(self) -> dict[str, str]:
        return dict(self._owners)


__all__ = ["CommandRegistry", "Handler", "RouteRegistry", "RouteSpec"]
