"""底座装配、过滤、报告(设计文档 §3 / §8.3 / §9)。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from helpers import Project

from agentscope_sidecar import CliOptions, Sidecar
from agentscope_sidecar.app import BUILTIN_PLUGINS_DIR, boot_sidecar
from agentscope_sidecar.convention import resolve
from agentscope_sidecar.loader import PluginState


TOOL = '''
from agentscope.agent import Agent
from agentscope.tool import FunctionTool, ToolResponse
from agentscope.message import TextBlock


async def {fn}(city: str = "x") -> ToolResponse:
    """工具 {fn}。

    Args:
        city (str): 城市
    """
    return ToolResponse(content=[TextBlock(type="text", text=city)])


async def setup(agent: Agent):
    await agent.toolkit.add_tool(FunctionTool({fn}))
'''

MIDDLEWARE = '''
from agentscope.middleware import MiddlewareBase


class {cls}(MiddlewareBase):
    async def on_model_call(self, agent, input_kwargs, next_handler):
        return await next_handler(**input_kwargs)


async def setup(agent):
    agent._{bucket}.append({cls}())
'''


def tool_plugin(name: str) -> str:
    return TOOL.format(fn=f"{name}_tool")


def middleware_plugin(name: str, cls: str, bucket: str = "model_call_middlewares") -> str:
    return MIDDLEWARE.format(cls=cls, bucket=bucket)


# ------------------------------------------------------------------ 启动


def test_boot_registers_base_services(project: Project) -> None:
    sidecar = project.boot()
    try:
        for name in ("agent", "options", "events", "telemetry", "routes", "commands"):
            assert sidecar.ctx.has(name), name
    finally:
        sidecar.shutdown()


def test_double_boot_raises(project: Project) -> None:
    sidecar = project.boot()
    try:
        with pytest.raises(RuntimeError) as exc:
            sidecar.boot()
        assert "已经启动" in str(exc.value)
    finally:
        sidecar.shutdown()


def test_boot_without_plugins_dir(project: Project) -> None:
    """空目录也能启动 —— 只剩内置插件。"""
    import shutil

    shutil.rmtree(project.root / "plugins")
    sidecar = project.boot()
    try:
        assert sidecar.loader.records["system_prompt"].state is PluginState.LOADED
    finally:
        sidecar.shutdown()


def test_build_agent_before_boot_raises(project: Project) -> None:
    sidecar = Sidecar(resolve(CliOptions(), cwd=project.root))
    with pytest.raises(RuntimeError):
        sidecar.build_agent()


def test_boot_sidecar_helper(project: Project) -> None:
    sidecar = boot_sidecar(CliOptions(), cwd=project.root)
    try:
        assert sidecar.model_ref is not None
    finally:
        sidecar.shutdown()


def test_aboot_from_event_loop(project: Project) -> None:
    """已经跑在事件循环里时用 ``aboot``。"""
    async def main() -> None:
        options = resolve(CliOptions(), cwd=project.root)
        sidecar = Sidecar(options)
        await sidecar.aboot()
        try:
            assert sidecar.build_agent() is not None
        finally:
            await sidecar.ashutdown()

    asyncio.run(main())


def test_permission_mode_reaches_agent(project: Project) -> None:
    sidecar = project.boot(permission="bypass")
    try:
        context = sidecar.build_agent().state.permission_context
        assert context.mode.value == "bypass"
    finally:
        sidecar.shutdown()


# ------------------------------------------------------------------ 过滤


def test_tools_filter_keeps_only_listed(project: Project) -> None:
    project.plugin("alpha", tool_plugin("alpha"))
    project.plugin("beta", tool_plugin("beta"))
    sidecar = project.boot(tools=("alpha_tool",))
    try:
        names = [
            t.name for g in sidecar.build_agent().toolkit.tool_groups for t in g.tools
        ]
        assert names == ["alpha_tool"]
    finally:
        sidecar.shutdown()


def test_tools_filter_empty_tuple_removes_all(project: Project) -> None:
    project.plugin("alpha", tool_plugin("alpha"))
    sidecar = project.boot(tools=())
    try:
        names = [
            t.name for g in sidecar.build_agent().toolkit.tool_groups for t in g.tools
        ]
        assert names == []
    finally:
        sidecar.shutdown()


def test_no_tools_filter_keeps_everything(project: Project) -> None:
    project.plugin("alpha", tool_plugin("alpha"))
    project.plugin("beta", tool_plugin("beta"))
    sidecar = project.boot()
    try:
        names = [
            t.name for g in sidecar.build_agent().toolkit.tool_groups for t in g.tools
        ]
        assert names == ["alpha_tool", "beta_tool"]
    finally:
        sidecar.shutdown()


def test_middlewares_filter_by_class_name(project: Project) -> None:
    project.plugin("a", middleware_plugin("a", "KeepMe"))
    project.plugin("b", middleware_plugin("b", "DropMe"))
    sidecar = project.boot(middlewares=("KeepMe",))
    try:
        agent = sidecar.build_agent()
        assert [type(m).__name__ for m in agent._model_call_middlewares] == ["KeepMe"]
    finally:
        sidecar.shutdown()


def test_middlewares_filter_empty_removes_all(project: Project) -> None:
    project.plugin("a", middleware_plugin("a", "DropMe"))
    sidecar = project.boot(middlewares=())
    try:
        assert sidecar.build_agent()._model_call_middlewares == []
    finally:
        sidecar.shutdown()


def test_filter_applies_to_all_buckets(project: Project) -> None:
    project.plugin("a", middleware_plugin("a", "KeepMe", "reply_middlewares"))
    project.plugin("b", middleware_plugin("b", "DropMe", "acting_middlewares"))
    sidecar = project.boot(middlewares=("KeepMe",))
    try:
        agent = sidecar.build_agent()
        assert [type(m).__name__ for m in agent._reply_middlewares] == ["KeepMe"]
        assert agent._acting_middlewares == []
    finally:
        sidecar.shutdown()


# ------------------------------------------------------------------ 报告


def test_report_shape(project: Project) -> None:
    project.plugin("alpha", tool_plugin("alpha"))
    sidecar = project.boot()
    try:
        report = sidecar.report()
        for key in (
            "base_dir", "plugin_dirs", "extensions", "model", "mode", "permission",
            "plugins", "warnings", "tools", "tool_owners", "middlewares", "routes",
            "services", "unresolved", "agent_overrides", "telemetry",
        ):
            assert key in report, key
    finally:
        sidecar.shutdown()


def test_report_lists_contributed(project: Project) -> None:
    project.plugin("alpha", tool_plugin("alpha"))
    sidecar = project.boot()
    try:
        report = sidecar.report()
        alpha = next(p for p in report["plugins"] if p["id"] == "alpha")
        assert alpha["contributed"] == {"tools": ["alpha_tool"]}
        assert report["tool_owners"] == {"alpha_tool": "alpha"}
    finally:
        sidecar.shutdown()


def test_report_plugin_order_marks_builtin(project: Project) -> None:
    sidecar = project.boot()
    try:
        report = sidecar.report()
        builtin = next(p for p in report["plugins"] if p["id"] == "system_prompt")
        assert builtin["order"] == -1
    finally:
        sidecar.shutdown()


def test_report_includes_warnings(project: Project) -> None:
    (project.root / "plugins" / "forgot").mkdir(parents=True)
    project.write("plugins/forgot/code.py", "x = 1\n")
    sidecar = project.boot()
    try:
        assert any("__init__.py" in w for w in sidecar.report()["warnings"])
    finally:
        sidecar.shutdown()


def test_report_failed_plugin_has_error(project: Project) -> None:
    project.plugin("boom", "async def setup():\n    raise ValueError('炸')\n")
    sidecar = project.boot()
    try:
        failed = next(p for p in sidecar.report()["plugins"] if p["id"] == "boom")
        assert failed["state"] == "failed"
        assert "炸" in failed["error"]
    finally:
        sidecar.shutdown()


def test_report_routes_and_commands(project: Project) -> None:
    project.plugin("web", '''
async def handler(request):
    return None


async def setup(routes, commands):
    routes.add("GET", "/hello", handler)
    commands.add("greet", lambda args: None)
''')
    sidecar = project.boot()
    try:
        report = sidecar.report()
        assert report["routes"] == {"GET /hello": "web"}
        assert sidecar.commands.names() == ["greet"]
    finally:
        sidecar.shutdown()


# ------------------------------------------------------------------ 事件转发


class _FakeEvent:
    def __init__(self, **kwargs) -> None:
        self.__dict__.update(kwargs)
        self.type = kwargs.get("type", "UNKNOWN")


def test_forward_event_emits_to_bus(project: Project) -> None:
    sidecar = project.boot()
    try:
        seen: list[dict] = []
        sidecar.bus.on("agent/event", lambda **kw: seen.append(kw))
        asyncio.run(sidecar.forward_event(_FakeEvent(
            type="TOOL_CALL_START", tool_call_name="x",
        )))
        assert seen[0]["type"] == "tool_call_start"
        assert seen[0]["summary"] == "tool_call:x"
    finally:
        sidecar.shutdown()


def test_forward_event_summarizes_each_kind(project: Project) -> None:
    sidecar = project.boot()
    try:
        seen: list[dict] = []
        sidecar.bus.on("agent/event", lambda **kw: seen.append(kw))
        for event in (
            _FakeEvent(type="MODEL_CALL_START", model_name="m"),
            _FakeEvent(type="REPLY_END", finished_reason="done"),
            _FakeEvent(type="OTHER"),
        ):
            asyncio.run(sidecar.forward_event(event))
        assert [s["summary"] for s in seen] == ["model:m", "finished:done", "other"]
    finally:
        sidecar.shutdown()


def test_non_interactive_confirm_warns(project: Project, caplog) -> None:
    """非交互模式下需要确认 —— 工具不会执行,必须告警。"""
    import logging

    sidecar = project.boot(mode="json", task="x")
    try:
        with caplog.at_level(logging.WARNING, logger="sidecar"):
            asyncio.run(sidecar.forward_event(_FakeEvent(
                type="REQUIRE_USER_CONFIRM",
                tool_calls=[_FakeEvent(name="danger")],
            )))
        assert "无法交互" in caplog.text
        assert "danger" in caplog.text
    finally:
        sidecar.shutdown()


def test_interactive_confirm_does_not_warn(project: Project, caplog) -> None:
    import logging

    sidecar = project.boot(mode="console")
    try:
        with caplog.at_level(logging.WARNING, logger="sidecar"):
            asyncio.run(sidecar.forward_event(_FakeEvent(
                type="REQUIRE_USER_CONFIRM", tool_calls=[],
            )))
        assert "无法交互" not in caplog.text
    finally:
        sidecar.shutdown()


# ------------------------------------------------------------------ 卸载与重载


def test_unload_clears_agent_cache(project: Project) -> None:
    project.plugin("alpha", tool_plugin("alpha"))
    sidecar = project.boot()
    try:
        asyncio.run(sidecar.unload("alpha"))
        assert sidecar.loader.records["alpha"].state is PluginState.UNLOADED
    finally:
        sidecar.shutdown()


def test_reload_emits_event(project: Project) -> None:
    sidecar = project.boot()
    try:
        seen: list[dict] = []
        sidecar.bus.on("sidecar/reloaded", lambda **kw: seen.append(kw))
        asyncio.run(sidecar.reload())
        assert len(seen) == 1
    finally:
        sidecar.shutdown()


def test_reload_reapplies_tools_filter(project: Project) -> None:
    project.plugin("alpha", tool_plugin("alpha"))
    sidecar = project.boot(tools=())
    try:
        asyncio.run(sidecar.reload())
        names = [
            t.name for g in sidecar.build_agent().toolkit.tool_groups for t in g.tools
        ]
        assert names == []
    finally:
        sidecar.shutdown()


# ------------------------------------------------------------------ 生命周期事件


def test_lifecycle_events_emitted(project: Project) -> None:
    project.plugin("alpha", tool_plugin("alpha"))
    sidecar = project.boot()
    try:
        counters = sidecar.report()["telemetry"]["counters"]
        assert counters["sidecar/boot"] == 1
        assert counters["sidecar/ready"] == 1
        assert counters["sidecar/plugin-loaded"] >= 3   # 两个内置 + alpha
    finally:
        sidecar.shutdown()


def test_shutdown_emits_event(project: Project) -> None:
    sidecar = project.boot()
    sidecar.shutdown()
    assert sidecar.telemetry.counters["sidecar/shutdown"] == 1
