"""路由与命令注册表(设计文档 §6.3 / §6.4)。"""

from __future__ import annotations

import pytest

from agentscope_sidecar.context import owned_by
from agentscope_sidecar.errors import DeclarationError
from agentscope_sidecar.services import CommandRegistry, RouteRegistry


# ------------------------------------------------------------------ 路由


def test_add_and_describe() -> None:
    routes = RouteRegistry()
    with owned_by("alpha"):
        routes.add("GET", "/x", lambda r: None)
    assert routes.describe() == {"GET /x": "alpha"}
    assert len(routes.routes()) == 1


def test_method_is_uppercased() -> None:
    routes = RouteRegistry()
    routes.add("get", "/x", lambda r: None)
    assert routes.routes()[0].method == "GET"


def test_bad_method_raises() -> None:
    routes = RouteRegistry()
    with pytest.raises(DeclarationError) as exc:
        routes.add("FETCH", "/x", lambda r: None)
    assert "HTTP 方法非法" in str(exc.value)


def test_path_must_start_with_slash() -> None:
    routes = RouteRegistry()
    with pytest.raises(DeclarationError) as exc:
        routes.add("GET", "x", lambda r: None)
    assert "必须以 / 开头" in str(exc.value)


def test_duplicate_route_raises_with_both_owners() -> None:
    routes = RouteRegistry()
    with owned_by("alpha"):
        routes.add("GET", "/x", lambda r: None)
    with owned_by("beta"):
        with pytest.raises(DeclarationError) as exc:
            routes.add("GET", "/x", lambda r: None)
    assert "alpha" in str(exc.value) and "beta" in str(exc.value)


def test_same_path_different_method_ok() -> None:
    routes = RouteRegistry()
    routes.add("GET", "/x", lambda r: None)
    routes.add("POST", "/x", lambda r: None)
    assert len(routes.routes()) == 2


def test_remove() -> None:
    routes = RouteRegistry()
    routes.add("GET", "/x", lambda r: None)
    routes.remove("get", "/x")
    assert routes.routes() == []
    routes.remove("GET", "/x")                 # 幂等


def test_routes_returns_a_copy() -> None:
    routes = RouteRegistry()
    routes.add("GET", "/x", lambda r: None)
    routes.routes().clear()
    assert len(routes.routes()) == 1


def test_handler_is_kept_as_is() -> None:
    routes = RouteRegistry()
    handler = lambda r: "ok"                   # noqa: E731
    routes.add("GET", "/x", handler)
    assert routes.routes()[0].handler is handler


# ------------------------------------------------------------------ 命令


def test_command_add_and_get() -> None:
    commands = CommandRegistry()
    handler = lambda args: None                # noqa: E731
    commands.add("run", handler)
    assert commands.get("run") is handler
    assert commands.names() == ["run"]


def test_command_duplicate_raises() -> None:
    commands = CommandRegistry()
    commands.add("run", lambda args: None)
    with pytest.raises(DeclarationError) as exc:
        commands.add("run", lambda args: None)
    assert "已被" in str(exc.value)
    assert "不能重复注册" in str(exc.value)


def test_command_records_owner() -> None:
    """命令和路由一样记归属 —— 加载器要能按插件回滚。"""
    from agentscope_sidecar.context import owned_by

    commands = CommandRegistry()
    with owned_by("alpha"):
        commands.add("one", lambda args: None)
    with owned_by("beta"):
        commands.add("two", lambda args: None)

    assert commands.describe() == {"one": "alpha", "two": "beta"}
    assert sorted(commands.remove_owned("alpha")) == ["one"]
    assert commands.names() == ["two"]
    assert commands.describe() == {"two": "beta"}


def test_command_remove_owned_is_empty_for_unknown_owner() -> None:
    commands = CommandRegistry()
    commands.add("run", lambda args: None)
    assert commands.remove_owned("nope") == []
    assert commands.names() == ["run"]


def test_command_remove_clears_owner() -> None:
    commands = CommandRegistry()
    commands.add("run", lambda args: None)
    commands.remove("run")
    commands.add("run", lambda args: None)     # 归属被清干净,可以重注册
    assert commands.describe() == {"run": "<base>"}


def test_command_remove() -> None:
    commands = CommandRegistry()
    commands.add("run", lambda args: None)
    commands.remove("run")
    assert commands.get("run") is None
    commands.remove("run")                     # 幂等


def test_command_names_sorted() -> None:
    commands = CommandRegistry()
    for name in ("c", "a", "b"):
        commands.add(name, lambda args: None)
    assert commands.names() == ["a", "b", "c"]
