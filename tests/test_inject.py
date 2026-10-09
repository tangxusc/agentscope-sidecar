"""签名依赖注入(设计文档 §5.2)。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agentscope_sidecar.context import Context
from agentscope_sidecar.convention import CliOptions, resolve
from agentscope_sidecar.errors import DeclarationError
from agentscope_sidecar.events import EventBus
from agentscope_sidecar.inject import resolve_arguments


class Widget:
    """按类型注入用的类型。"""


class Gadget:
    pass


def make_ctx(tmp_path: Path | None = None) -> Context:
    return Context(resolve(CliOptions(), cwd=tmp_path or Path.cwd()), EventBus())


# ------------------------------------------------------------------ 规则 0:ctx


def test_ctx_gets_the_plugin_view(tmp_path: Path) -> None:
    """参数名 ``ctx`` 注入**当前插件的视图**,不是根容器。"""
    root = make_ctx(tmp_path)
    view = root.for_plugin("mine", tmp_path, tmp_path)

    async def setup(ctx):
        pass

    kwargs, _ = resolve_arguments(setup, view)
    assert kwargs["ctx"] is view
    assert kwargs["ctx"].id == "mine"


# ------------------------------------------------------------------ 规则 1:_ 前缀


def test_underscore_prefixed_is_skipped() -> None:
    async def setup(_ignored=None):
        pass

    kwargs, _ = resolve_arguments(setup, make_ctx())
    assert kwargs == {}


def test_underscore_without_default_raises() -> None:
    async def setup(_ignored):
        pass

    with pytest.raises(DeclarationError) as exc:
        resolve_arguments(setup, make_ctx())
    assert "必须给默认值" in str(exc.value)


# ------------------------------------------------------------------ 规则 2:按名称


def test_inject_by_name() -> None:
    ctx = make_ctx()
    ctx.provide("events", "EVENTS")

    async def setup(events):
        pass

    kwargs, unresolved = resolve_arguments(setup, ctx)
    assert kwargs == {"events": "EVENTS"}
    assert unresolved == []


def test_name_wins_over_type() -> None:
    """名称精确、类型宽松 —— 同时存在时按名称。"""
    ctx = make_ctx()
    ctx.provide("widget", "BY-NAME")
    ctx.provide("other", Widget())

    async def setup(widget: Widget):
        pass

    kwargs, _ = resolve_arguments(setup, ctx)
    assert kwargs["widget"] == "BY-NAME"


# ------------------------------------------------------------------ 规则 3 / 4:按类型


def test_inject_by_type() -> None:
    ctx = make_ctx()
    widget = Widget()
    ctx.provide("some_name", widget)

    async def setup(w: Widget):
        pass

    kwargs, _ = resolve_arguments(setup, ctx)
    assert kwargs["w"] is widget


def test_ambiguous_type_raises() -> None:
    ctx = make_ctx()
    ctx.provide("a", Widget())
    ctx.provide("b", Widget())

    async def setup(w: Widget):
        pass

    with pytest.raises(DeclarationError) as exc:
        resolve_arguments(setup, ctx)
    assert "匹配到多个服务" in str(exc.value)


def test_type_not_in_container_falls_through() -> None:
    """类型匹配不到 → 不当场报错,交给后面的规则。"""
    ctx = make_ctx()

    async def setup(w: Widget):
        pass

    kwargs, unresolved = resolve_arguments(setup, ctx)
    assert unresolved == ["w"]                 # 拿到惰性代理


def test_annotation_is_resolved_from_string() -> None:
    """插件文件普遍写 ``from __future__ import annotations`` —— 注解是字符串。

    本模块顶部就有 ``from __future__ import annotations``,所以这个测试
    天然覆盖了 ``get_type_hints`` 的还原路径。
    """
    ctx = make_ctx()
    gadget = Gadget()
    ctx.provide("g", gadget)

    async def setup(g: Gadget):
        pass

    kwargs, _ = resolve_arguments(setup, ctx)
    assert kwargs["g"] is gadget


def test_broken_annotation_falls_back_to_name() -> None:
    """注解引用了不存在的名字 → 吞掉异常,退回按名称。"""
    ctx = make_ctx()

    namespace: dict[str, Any] = {}
    exec(
        "from __future__ import annotations\n"
        "async def setup(thing: NoSuchType):\n"
        "    pass\n",
        namespace,
    )

    kwargs, unresolved = resolve_arguments(namespace["setup"], ctx)
    assert unresolved == ["thing"]             # 没有炸,拿到代理


# ------------------------------------------------------------------ 规则 5:默认值


def test_default_used_when_service_missing() -> None:
    async def setup(optional="DEFAULT"):
        pass

    kwargs, unresolved = resolve_arguments(setup, make_ctx())
    assert kwargs == {}                        # 不传 → 用签名里的默认值
    assert unresolved == []


def test_service_overrides_default() -> None:
    ctx = make_ctx()
    ctx.provide("optional", "FROM-CONTAINER")

    async def setup(optional="DEFAULT"):
        pass

    kwargs, _ = resolve_arguments(setup, ctx)
    assert kwargs == {"optional": "FROM-CONTAINER"}


# ------------------------------------------------------------------ 规则 6:惰性代理


def test_missing_becomes_lazy_proxy() -> None:
    ctx = make_ctx()

    async def setup(later):
        pass

    kwargs, unresolved = resolve_arguments(setup, ctx)
    assert unresolved == ["later"]
    assert "pending" in repr(kwargs["later"])


# ------------------------------------------------------------------ 签名限制


def test_varargs_rejected() -> None:
    async def setup(*args):
        pass

    with pytest.raises(DeclarationError) as exc:
        resolve_arguments(setup, make_ctx())
    assert "*args" in str(exc.value)


def test_kwargs_rejected() -> None:
    async def setup(**kwargs):
        pass

    with pytest.raises(DeclarationError) as exc:
        resolve_arguments(setup, make_ctx())
    assert "**kwargs" in str(exc.value)


def test_mixed_parameters() -> None:
    ctx = make_ctx()
    ctx.provide("options", "OPT")
    ctx.provide("w", Widget())

    async def setup(
        ctx, options, w: Widget, missing, _skip=None, optional="D",
    ):
        pass

    kwargs, unresolved = resolve_arguments(setup, ctx)
    assert kwargs["ctx"] is ctx
    assert kwargs["options"] == "OPT"
    assert kwargs["w"] is ctx.get("w")          # 按类型找到唯一匹配
    assert unresolved == ["missing"]
    assert "_skip" not in kwargs
    assert "optional" not in kwargs
