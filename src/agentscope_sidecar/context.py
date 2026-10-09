"""服务容器与惰性引用(设计文档 §5 / §6)。

容器是**全局唯一**的;每个插件拿到的 ``ctx`` 是一个**视图** —— 共享同一份服务
存储,但自带身份(``id`` / ``dir`` / ``logger``)。``ctx.provide()`` 注册服务时
按视图的身份记归属。
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

from .convention import RuntimeOptions
from .errors import DeclarationError, ServiceNotFound
from .events import EventBus

#: 底座自己注册服务时用的归属名。
BASE_OWNER = "<base>"

#: 当前正在跑的插件 id。加载器在调用 ``setup`` 期间设置。
#:
#: 路由、命令这类**不在 Agent 上**的东西没法靠快照差分归属,只能由注册表在
#: 注册时读这里。用 ContextVar 而不是普通全局 —— 异步宿主可能并发跑多个插件。
_current_owner: ContextVar[str] = ContextVar("sidecar_owner", default=BASE_OWNER)


def current_owner() -> str:
    """当前正在执行的插件 id。不在插件里时是 ``<base>``。"""
    return _current_owner.get()


@contextmanager
def owned_by(plugin_id: str) -> Iterator[None]:
    """把这段代码里发生的注册记为 ``plugin_id`` 的。"""
    token = _current_owner.set(plugin_id)
    try:
        yield
    finally:
        _current_owner.reset(token)


@dataclass
class _Store:
    """服务存储。容器与全部插件视图共享同一份。"""

    services: dict[str, Any] = field(default_factory=dict)
    owners: dict[str, str] = field(default_factory=dict)
    types: dict[type, list[str]] = field(default_factory=dict)


class ServiceRef:
    """惰性服务引用。

    每次访问时重新解析,因此服务提供方被替换后自动生效。

    除属性访问外还代理容器协议(``[]`` / ``len`` / ``in`` / 迭代)。少了这些,
    ``ctx.require("x")["k"]`` 会抛 ``TypeError: not subscriptable`` ——
    一个和「服务不存在」毫无关系的报错,排查方向直接被带偏。
    """

    __slots__ = ("_store", "_name")

    def __init__(self, store: _Store, name: str) -> None:
        object.__setattr__(self, "_store", store)
        object.__setattr__(self, "_name", name)

    def _resolve(self) -> Any:
        store = object.__getattribute__(self, "_store")
        name = object.__getattribute__(self, "_name")
        if name not in store.services:
            raise ServiceNotFound(name, sorted(store.services))
        return store.services[name]

    def __getattr__(self, item: str) -> Any:
        return getattr(self._resolve(), item)

    def __setattr__(self, key: str, value: Any) -> None:
        setattr(self._resolve(), key, value)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self._resolve()(*args, **kwargs)

    def __getitem__(self, key: Any) -> Any:
        return self._resolve()[key]

    def __setitem__(self, key: Any, value: Any) -> None:
        self._resolve()[key] = value

    def __delitem__(self, key: Any) -> None:
        del self._resolve()[key]

    def __iter__(self) -> Any:
        return iter(self._resolve())

    def __len__(self) -> int:
        return len(self._resolve())

    def __contains__(self, item: Any) -> bool:
        return item in self._resolve()

    def __str__(self) -> str:
        """``f"{ref}"`` 要打出服务本身的值。

        只转发 ``__repr__`` 不够 —— ``f"{ref}"`` 走的是 ``object.__str__``,
        而它会退到我们自己的 ``__repr__``,于是打印出 ``<ServiceRef x (ready)>``。
        **看起来像没解析成功,其实是解析了但打印错了。**
        """
        return str(self._resolve())

    def __format__(self, spec: str) -> str:
        return format(self._resolve(), spec)

    def __bool__(self) -> bool:
        return bool(self._resolve())

    def __repr__(self) -> str:
        store = object.__getattribute__(self, "_store")
        name = object.__getattribute__(self, "_name")
        state = "ready" if name in store.services else "pending"
        return f"<ServiceRef {name} ({state})>"


class Context:
    """服务容器,同时是注入给插件的 ``ctx``。

    插件视图由 :meth:`for_plugin` 派生 —— 共享 :class:`_Store`,身份不同。
    """

    def __init__(
        self,
        options: RuntimeOptions,
        bus: EventBus,
        *,
        store: _Store | None = None,
        plugin_id: str = BASE_OWNER,
        plugin_dir: Path | None = None,
        root: Path | None = None,
    ) -> None:
        self.options = options
        self.events = bus
        self._store = store if store is not None else _Store()
        self._id = plugin_id
        self._dir = plugin_dir if plugin_dir is not None else options.base_dir
        self._root = root if root is not None else self._dir
        self.logger = logging.getLogger(
            "sidecar" if plugin_id == BASE_OWNER else f"sidecar.plugin.{plugin_id}",
        )

    # ------------------------------------------------------------------ 身份

    @property
    def id(self) -> str:
        return self._id

    @property
    def dir(self) -> Path:
        """插件自身所在目录(目录形态时为包目录)。"""
        return self._dir

    @property
    def root(self) -> Path:
        """来源插件目录。"""
        return self._root

    @property
    def interactive(self) -> bool:
        """运行模式是否支持交互 —— 只有 ``console`` 为 True。"""
        return self.options.mode == "console"

    def for_plugin(
        self, plugin_id: str, plugin_dir: Path, root: Path,
    ) -> "Context":
        """派生一个绑定到某插件的视图。共享服务存储。"""
        return Context(
            self.options, self.events, store=self._store,
            plugin_id=plugin_id, plugin_dir=plugin_dir, root=root,
        )

    # ------------------------------------------------------------------ 服务

    def provide(self, name: str, value: Any) -> None:
        """注册服务。同名被不同归属注册时抛 ``DeclarationError``。"""
        store = self._store
        existing = store.owners.get(name)
        if existing is not None and existing != self._id:
            raise DeclarationError(
                f"服务 {name!r} 已被 {existing!r} 注册,{self._id!r} 不能重复注册",
            )
        if name in store.services:
            self._forget(name)                       # 同一归属覆盖:先清索引
        store.services[name] = value
        store.owners[name] = self._id
        store.types.setdefault(type(value), []).append(name)

    def remove(self, name: str) -> None:
        """移除服务。不存在时静默返回。"""
        if name in self._store.services:
            self._forget(name)

    def remove_owned(self, owner: str) -> list[str]:
        """移除某个归属注册的全部服务。返回被移除的名字。

        加载器在 ``setup`` 半途抛异常、或插件被卸载时用它回滚 —— 没有这层,
        失败插件留下的服务会一直挂在容器里,而报告里它已经是 ``failed``。
        """
        removed = [
            name for name, who in self._store.owners.items() if who == owner
        ]
        for name in removed:
            self._forget(name)
        return removed

    def _forget(self, name: str) -> None:
        store = self._store
        value = store.services.pop(name, None)
        store.owners.pop(name, None)
        if value is None:
            return
        bucket = store.types.get(type(value))
        if bucket and name in bucket:
            bucket.remove(name)
        if bucket is not None and not bucket:
            store.types.pop(type(value), None)

    def get(self, name: str, default: Any = None) -> Any:
        """立即解析。服务不存在时返回 ``default``。用于软依赖。"""
        return self._store.services.get(name, default)

    def require(self, name: str) -> Any:
        """解析服务。已就绪返回真身,未就绪返回惰性代理。"""
        if name in self._store.services:
            return self._store.services[name]
        return ServiceRef(self._store, name)

    def has(self, name: str) -> bool:
        """服务是否已注册。

        与 ``get(name) is not None`` 的区别:服务值本身可能是 ``None``,
        那种情况也算已注册。
        """
        return name in self._store.services

    def services(self) -> dict[str, str]:
        """服务名 -> 归属。"""
        return dict(self._store.owners)

    def names(self) -> list[str]:
        return sorted(self._store.services)

    def by_type(self, cls: type) -> list[str]:
        """按类型查服务名。**精确匹配 ``type(value)``,不做子类匹配。**"""
        return list(self._store.types.get(cls, ()))

    # ------------------------------------------------------------------ 便捷

    def on(self, event: str, handler: Callable[..., Any]) -> Callable[[], None]:
        """订阅事件。返回退订函数(与 ``events.on`` 相同)。"""
        return self.events.on(event, handler)

    def __repr__(self) -> str:
        return f"<Context {self._id} services={len(self._store.services)}>"


__all__ = [
    "BASE_OWNER",
    "Context",
    "ServiceRef",
    "current_owner",
    "owned_by",
]
