"""服务容器与惰性引用(设计文档 §5.2 / §6)。"""

from __future__ import annotations

import pytest

from helpers import Project

from agentscope_sidecar.context import (
    BASE_OWNER,
    Context,
    ServiceRef,
    current_owner,
    owned_by,
)
from agentscope_sidecar.convention import CliOptions, resolve
from agentscope_sidecar.errors import DeclarationError, ServiceNotFound
from agentscope_sidecar.events import EventBus


def make_ctx(tmp_path=None) -> Context:
    from pathlib import Path

    options = resolve(CliOptions(), cwd=tmp_path or Path.cwd())
    return Context(options, EventBus())


# ------------------------------------------------------------------ 服务基本操作


def test_provide_and_get() -> None:
    ctx = make_ctx()
    ctx.provide("x", 42)
    assert ctx.get("x") == 42
    assert ctx.has("x") is True


def test_get_missing_returns_default() -> None:
    ctx = make_ctx()
    assert ctx.get("nope") is None
    assert ctx.get("nope", "fallback") == "fallback"


def test_has_distinguishes_none_value() -> None:
    """服务值本身可以是 None —— ``has`` 不能退化成 ``get(...) is not None``。"""
    ctx = make_ctx()
    ctx.provide("nothing", None)
    assert ctx.has("nothing") is True
    assert ctx.get("nothing") is None


def test_require_returns_real_object_when_ready() -> None:
    ctx = make_ctx()
    ctx.provide("x", [1, 2])
    assert ctx.require("x") == [1, 2]


def test_require_returns_proxy_when_pending() -> None:
    ctx = make_ctx()
    ref = ctx.require("later")
    assert isinstance(ref, ServiceRef)
    assert "pending" in repr(ref)

    ctx.provide("later", {"k": 1})
    assert ref["k"] == 1
    assert "ready" in repr(ref)


def test_duplicate_name_from_other_owner_raises() -> None:
    ctx = make_ctx()
    ctx.provide("x", 1)
    other = ctx.for_plugin("other", ctx.dir, ctx.root)
    with pytest.raises(DeclarationError) as exc:
        other.provide("x", 2)
    assert "已被" in str(exc.value)


def test_same_owner_can_overwrite() -> None:
    ctx = make_ctx()
    ctx.provide("x", 1)
    ctx.provide("x", 2)
    assert ctx.get("x") == 2
    assert ctx.by_type(int) == ["x"]           # 索引不重复


def test_remove() -> None:
    ctx = make_ctx()
    ctx.provide("x", 1)
    ctx.remove("x")
    assert ctx.has("x") is False
    ctx.remove("x")                            # 幂等


def test_remove_cleans_type_index() -> None:
    ctx = make_ctx()
    ctx.provide("x", 1)
    assert ctx.by_type(int) == ["x"]
    ctx.remove("x")
    assert ctx.by_type(int) == []


def test_names_and_services() -> None:
    ctx = make_ctx()
    ctx.provide("b", 1)
    ctx.provide("a", 2)
    assert ctx.names() == ["a", "b"]
    assert ctx.services() == {"a": BASE_OWNER, "b": BASE_OWNER}


def test_remove_of_none_value_cleans_owner() -> None:
    ctx = make_ctx()
    ctx.provide("x", None)
    ctx.remove("x")
    assert ctx.services() == {}


# ------------------------------------------------------------------ 类型索引


def test_by_type_exact_match_only() -> None:
    """**不做子类匹配** —— 否则「基类注解注入了哪个子类」变成运行时玄学。"""
    ctx = make_ctx()

    class Base:
        pass

    class Sub(Base):
        pass

    ctx.provide("sub", Sub())
    assert ctx.by_type(Sub) == ["sub"]
    assert ctx.by_type(Base) == []


def test_by_type_returns_all_matches() -> None:
    ctx = make_ctx()
    ctx.provide("a", "s1")
    ctx.provide("b", "s2")
    assert sorted(ctx.by_type(str)) == ["a", "b"]


def test_by_type_unknown_returns_empty() -> None:
    ctx = make_ctx()
    assert ctx.by_type(int) == []


# ------------------------------------------------------------------ 插件视图


def test_for_plugin_shares_store_but_not_identity(project: Project) -> None:
    ctx = make_ctx(project.root)
    view = ctx.for_plugin("mine", project.root, project.root)
    ctx.provide("shared", 1)
    assert view.get("shared") == 1             # 共享存储
    assert view.id == "mine"
    assert ctx.id == BASE_OWNER


def test_plugin_view_records_ownership(project: Project) -> None:
    ctx = make_ctx(project.root)
    view = ctx.for_plugin("mine", project.root, project.root)
    view.provide("x", 1)
    assert ctx.services() == {"x": "mine"}


def test_view_identity_properties(project: Project) -> None:
    ctx = make_ctx(project.root)
    view = ctx.for_plugin("mine", project.root / "plugins", project.root)
    assert view.dir == project.root / "plugins"
    assert view.root == project.root
    assert view.logger.name == "sidecar.plugin.mine"


def test_base_logger_name() -> None:
    ctx = make_ctx()
    assert ctx.logger.name == "sidecar"


def test_interactive_only_in_console(tmp_path) -> None:
    from pathlib import Path

    for mode, expected in (("console", True), ("once", False), ("http", False)):
        options = resolve(CliOptions(mode=mode), cwd=Path(tmp_path))
        assert Context(options, EventBus()).interactive is expected


def test_repr(project: Project) -> None:
    ctx = make_ctx(project.root)
    ctx.provide("x", 1)
    assert "services=1" in repr(ctx)


# ------------------------------------------------------------------ ServiceRef 协议


def test_service_ref_attribute_access() -> None:
    class Thing:
        def __init__(self) -> None:
            self.k = 1

    ctx = make_ctx()
    ctx.provide("obj", Thing())
    assert ctx.require("obj").k == 1


def test_service_ref_attribute_assignment() -> None:
    class Thing:
        pass

    target = Thing()
    ctx = make_ctx()
    ctx.provide("obj", target)
    ctx.require("obj").k = 7
    assert target.k == 7


def test_service_ref_call() -> None:
    ctx = make_ctx()
    ctx.provide("fn", lambda a, b=0: a + b)
    assert ctx.require("fn")(1, b=2) == 3


def test_service_ref_container_protocol() -> None:
    ctx = make_ctx()
    ctx.provide("d", {"a": 1})
    ref = ctx.require("d")
    assert list(ref) == ["a"]
    assert len(ref) == 1
    assert "a" in ref
    ref["b"] = 2
    assert ctx.get("d") == {"a": 1, "b": 2}
    del ref["a"]
    assert ctx.get("d") == {"b": 2}


def test_service_ref_missing_raises_with_available() -> None:
    ctx = make_ctx()
    ctx.provide("known", 1)
    with pytest.raises(ServiceNotFound) as exc:
        ctx.require("nope").anything
    assert "known" in str(exc.value)


def test_service_ref_resolves_late() -> None:
    """代理是**每次访问重新解析**的 —— 服务后来才注册也能用。"""
    ctx = make_ctx()
    ref = ctx.require("later")
    ctx.provide("later", "ok")
    assert ref.upper() == "OK"


# ------------------------------------------------------------------ 归属


def test_current_owner_defaults_to_base() -> None:
    assert current_owner() == BASE_OWNER


def test_owned_by_sets_and_restores() -> None:
    with owned_by("alpha"):
        assert current_owner() == "alpha"
        with owned_by("beta"):
            assert current_owner() == "beta"
        assert current_owner() == "alpha"
    assert current_owner() == BASE_OWNER


def test_owned_by_restores_on_exception() -> None:
    with pytest.raises(RuntimeError):
        with owned_by("alpha"):
            raise RuntimeError("boom")
    assert current_owner() == BASE_OWNER


# ------------------------------------------------------------------ 事件便捷


def test_ctx_on_returns_unsubscribe() -> None:
    ctx = make_ctx()
    seen: list[int] = []
    off = ctx.on("x", lambda **kw: seen.append(kw.get("n", 0)))
    assert ctx.events.listener_count("x") == 1
    off()
    assert ctx.events.listener_count("x") == 0
