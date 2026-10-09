"""签名依赖注入(设计文档 §5)。

``setup`` 的参数列表就是依赖声明 —— 没有 ``inject = [...]``,没有 ``@Autowired``。
两种解析方式:**按名称优先,按类型兜底**。
"""

from __future__ import annotations

import inspect
import logging
import typing
from typing import Any, Callable

from .context import Context
from .errors import DeclarationError

logger = logging.getLogger("sidecar.inject")

#: 解析结果。
Resolved = tuple[dict[str, Any], list[str]]


def resolve_arguments(func: Callable[..., Any], ctx: Context) -> Resolved:
    """按 ``func`` 的签名解析出调用参数。

    ``ctx`` 是**当前插件的视图**,不是根容器 —— 参数名叫 ``ctx`` 时注入它,
    这样 ``ctx.id`` / ``ctx.dir`` / ``ctx.provide`` 的归属都是对的。

    返回 ``(kwargs, unresolved)`` —— ``unresolved`` 是拿到惰性代理的参数名,
    供 ``inspect`` 报告显示(惰性不等于不可观测)。
    """
    try:
        sig = inspect.signature(func)
    except (TypeError, ValueError) as exc:                # pragma: no cover
        raise DeclarationError(f"无法读取 setup 的签名:{exc}") from exc

    hints = _type_hints(func)
    kwargs: dict[str, Any] = {}
    unresolved: list[str] = []

    for name, param in sig.parameters.items():
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            raise DeclarationError(
                f"setup 的签名不能有 *args / **kwargs(参数 {name!r})",
            )

        has_default = param.default is not inspect.Parameter.empty

        if name == "ctx":                                # 规则 0:插件自己的视图
            kwargs[name] = ctx
            continue

        if name.startswith("_"):                         # 规则 1
            if not has_default:
                raise DeclarationError(
                    f"参数 {name!r} 以 _ 开头表示不注入,必须给默认值",
                )
            continue

        if ctx.has(name):                                # 规则 2:按名称
            kwargs[name] = ctx.get(name)
            continue

        annotation = hints.get(name)
        if annotation is not None:                       # 规则 3 / 4:按类型
            matches = ctx.by_type(annotation)
            if len(matches) == 1:
                kwargs[name] = ctx.get(matches[0])
                continue
            if len(matches) > 1:
                raise DeclarationError(
                    f"参数 {name!r} 的类型 {annotation!r} 匹配到多个服务 "
                    f"{matches};请改用服务名做参数名,或用 ctx.get 显式指定",
                )

        if has_default:                                  # 规则 5
            continue

        kwargs[name] = ctx.require(name)                 # 规则 6:惰性代理
        unresolved.append(name)

    return kwargs, unresolved


def _type_hints(func: Callable[..., Any]) -> dict[str, Any]:
    """解析后的类型注解。

    插件文件普遍写 ``from __future__ import annotations``,那时
    ``param.annotation`` 是**字符串** ``'Agent'`` 而不是类 —— 直接比较永远不匹配。

    ``get_type_hints`` 在注解引用了不存在的名字时抛异常。**吞掉并退回按名称解析**:
    不能因为一个注解拼错就让插件加载失败。

    吞掉之后要留痕 —— 否则「注解写错了」和「这个参数本来就该按名字解析」长得
    一模一样,只能靠 ``--verbose`` 才看得出来。
    """
    try:
        return typing.get_type_hints(func)
    except Exception as exc:
        logger.debug(
            "无法解析 %s 的类型注解,退回按名称解析:%s",
            getattr(func, "__qualname__", func), exc,
        )
        return {}


__all__ = ["Resolved", "resolve_arguments"]
