"""插件加载与归属(设计文档 §3 / §8)。

跑的是 ``agent/`` 这份真实夹具 —— 13 个插件,覆盖单文件、目录形态、
无 setup、``_`` 前缀、内置插件。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentscope_sidecar import PluginState
from helpers import GENERIC_PLUGIN_NAMES

from expected import (
    EXCLUDED,
    MAIN_PLUGINS,
    ROUTES_BY_PLUGIN,
    SERVICES_BY_PLUGIN,
    TOOLS_BY_PLUGIN,
)

#: 底座自带的插件 —— 不属于夹具,断言时要排掉。
BUILTIN = ("skills", "system_prompt")


def _states(sidecar) -> dict[str, str]:
    return {
        item["id"]: item["state"]
        for item in sidecar.report()["plugins"]
        if item["id"] not in BUILTIN
    }


def test_every_fixture_plugin_is_accounted_for(e2e) -> None:
    sidecar = e2e.boot()
    try:
        assert _states(sidecar) == {
            k: v.value for k, v in MAIN_PLUGINS.items()
        }
    finally:
        sidecar.shutdown()


def test_builtin_plugins_are_loaded_first(e2e) -> None:
    """内置插件优先级最低 —— 排在最前,用户插件能覆盖它。"""
    sidecar = e2e.boot()
    try:
        order = [item["id"] for item in sidecar.report()["plugins"]]
        assert order[0] == "skills"
        assert order[1] == "system_prompt"
        assert order.index("weather") > order.index("system_prompt")
    finally:
        sidecar.shutdown()


def test_underscore_prefixed_is_excluded(e2e) -> None:
    """``_`` 前缀的条目**不进状态机、不出现在报告里**。"""
    sidecar = e2e.boot()
    try:
        ids = {item["id"] for item in sidecar.report()["plugins"]}
        for name in EXCLUDED:
            assert name not in ids
    finally:
        sidecar.shutdown()


def test_plugin_without_setup_is_skipped_not_failed(e2e) -> None:
    """``scratch`` 没有 setup —— 这是诊断信号,不是错误。"""
    sidecar = e2e.boot()
    try:
        record = sidecar.loader.records["scratch"]
        assert record.state is PluginState.SKIPPED
        assert record.error is None
        assert record.contributed == {}
    finally:
        sidecar.shutdown()


def test_package_plugin_uses_directory_name_as_id(e2e) -> None:
    sidecar = e2e.boot()
    try:
        record = sidecar.loader.records["weather"]
        assert record.state is PluginState.LOADED
        assert record.path.name == "__init__.py"
        assert record.path.parent.name == "weather"
    finally:
        sidecar.shutdown()


def test_package_private_module_is_not_a_plugin(e2e) -> None:
    """``weather/api_client.py`` 是配套文件,不该单独成为一个插件。"""
    sidecar = e2e.boot()
    try:
        assert "api_client" not in sidecar.loader.records
        ids = {item["id"] for item in sidecar.report()["plugins"]}
        assert "api_client" not in ids
    finally:
        sidecar.shutdown()


@pytest.mark.parametrize(
    ("plugin_id", "expected"),
    sorted(TOOLS_BY_PLUGIN.items()),
)
def test_tools_are_attributed_to_their_plugin(
    e2e, plugin_id: str, expected: tuple[str, ...],
) -> None:
    """快照差分得出归属 —— 插件直接 ``add_tool``,底座只观察结果。"""
    sidecar = e2e.boot()
    try:
        record = sidecar.loader.records[plugin_id]
        assert sorted(record.contributed.get("tools", [])) == sorted(expected)
    finally:
        sidecar.shutdown()


def test_tool_owners_report(e2e) -> None:
    sidecar = e2e.boot()
    try:
        owners = sidecar.report()["tool_owners"]
        flat = {
            tool: plugin_id
            for plugin_id, tools in TOOLS_BY_PLUGIN.items()
            for tool in tools
        }
        assert owners == flat
    finally:
        sidecar.shutdown()


def test_routes_are_attributed_to_their_plugin(e2e) -> None:
    """路由不在 Agent 上 —— 归属靠 ``current_owner()``,不是快照差分。"""
    sidecar = e2e.boot()
    try:
        assert sidecar.report()["routes"] == ROUTES_BY_PLUGIN
    finally:
        sidecar.shutdown()


def test_services_report(e2e) -> None:
    sidecar = e2e.boot()
    try:
        assert sidecar.report()["services"] == SERVICES_BY_PLUGIN
    finally:
        sidecar.shutdown()


def test_middlewares_are_attributed_to_their_plugin(e2e) -> None:
    sidecar = e2e.boot()
    try:
        assert sidecar.loader.records["audit"].contributed["middlewares"] == [
            "AuditTrail", "PromptSection",
        ]
        assert "middlewares" not in sidecar.loader.records["catalog"].contributed
    finally:
        sidecar.shutdown()


def test_agent_overrides_are_attributed(e2e) -> None:
    """Agent 构造参数被换过对象 —— 按身份比较判定,不是按值。"""
    sidecar = e2e.boot()
    try:
        record = sidecar.loader.records["agent_tuning"]
        assert sorted(record.contributed["agent_overrides"]) == [
            "context_config", "injection_config", "react_config",
        ]
        assert sidecar.report()["agent_overrides"] == {
            "context_config": "agent_tuning",
            "injection_config": "agent_tuning",
            "react_config": "agent_tuning",
        }
    finally:
        sidecar.shutdown()


def test_unload_runs_effect_and_drops_services(e2e) -> None:
    import asyncio

    sidecar = e2e.boot()
    try:
        assert sidecar.ctx.has("catalog")
        asyncio.run(sidecar.unload("catalog"))
        assert not sidecar.ctx.has("catalog")
        assert sidecar.loader.records["catalog"].state is PluginState.UNLOADED
    finally:
        sidecar.shutdown()


def test_unload_unknown_plugin_returns_false(e2e) -> None:
    import asyncio

    sidecar = e2e.boot()
    try:
        assert asyncio.run(sidecar.unload("nope")) is False
        assert asyncio.run(sidecar.unload("scratch")) is False   # SKIPPED 不算可卸载
    finally:
        sidecar.shutdown()


def test_unload_is_idempotent(e2e) -> None:
    import asyncio

    sidecar = e2e.boot()
    try:
        assert asyncio.run(sidecar.unload("catalog")) is True
        assert asyncio.run(sidecar.unload("catalog")) is False
    finally:
        sidecar.shutdown()


def test_boot_twice_is_rejected(e2e) -> None:
    sidecar = e2e.boot()
    try:
        with pytest.raises(RuntimeError, match="已经启动"):
            sidecar.boot()
    finally:
        sidecar.shutdown()


def test_build_agent_before_boot_is_rejected(e2e) -> None:
    from agentscope_sidecar import CliOptions, Sidecar
    from agentscope_sidecar.convention import resolve

    sidecar = Sidecar(resolve(CliOptions(), cwd=e2e.root))
    with pytest.raises(RuntimeError, match="未启动"):
        sidecar.build_agent()


def test_unload_emits_event(e2e) -> None:
    """卸载要广播出去 —— 插件消失对观察者不该是静默的。"""
    import asyncio

    e2e.plugin("watcher", '''
        from agentscope_sidecar import Context


        async def setup(ctx: Context, events) -> None:
            def record(**payload: object) -> None:
                seen = ctx.get("unloaded") or []
                ctx.provide("unloaded", [*seen, payload["plugin"]])

            events.on("sidecar/plugin-unloaded", record)
    ''')
    sidecar = e2e.boot()
    try:
        assert asyncio.run(sidecar.unload("catalog")) is True
        assert sidecar.ctx.get("unloaded") == ["catalog"]
    finally:
        sidecar.shutdown()


def test_failed_unload_emits_nothing(e2e) -> None:
    import asyncio

    sidecar = e2e.boot()
    try:
        counters_before = dict(sidecar.telemetry.counters)
        assert asyncio.run(sidecar.unload("nope")) is False
        assert sidecar.telemetry.counters.get(
            "sidecar/plugin-unloaded", 0,
        ) == counters_before.get("sidecar/plugin-unloaded", 0)
    finally:
        sidecar.shutdown()


def test_unload_removes_tools_and_middlewares(bare) -> None:
    """卸载要把工具与中间件摘掉 —— 只调 effect 不够。

    插件没写 effect(或写得不全)时,东西会留在 Agent 上,而记录已经是
    ``UNLOADED``。报告自相矛盾,调用方也拿不到「真的卸载了」这个保证。
    """
    import asyncio

    bare.plugin("toolbox", '''
        from agentscope.agent import Agent
        from agentscope.middleware import MiddlewareBase
        from agentscope.message import TextBlock
        from agentscope.tool import FunctionTool, ToolResponse


        class Marker(MiddlewareBase):
            async def on_model_call(self, agent, input_kwargs, next_handler):
                return await next_handler(**input_kwargs)


        async def ping() -> ToolResponse:
            """ping。"""
            return ToolResponse(content=[TextBlock(type="text", text="pong")])


        async def setup(agent: Agent) -> None:
            await agent.toolkit.add_tool(FunctionTool(ping, is_read_only=True))
            agent._model_call_middlewares.append(Marker())
    ''')
    # 这个故意不返回 effect —— 半成品插件
    bare.plugin("leaky", '''
        from agentscope.agent import Agent
        from agentscope.message import TextBlock
        from agentscope.tool import FunctionTool, ToolResponse


        async def leaky() -> ToolResponse:
            """没有 effect 撤销的工具。"""
            return ToolResponse(content=[TextBlock(type="text", text="x")])


        async def setup(agent: Agent) -> None:
            await agent.toolkit.add_tool(FunctionTool(leaky, is_read_only=True))
    ''')
    sidecar = bare.boot()
    try:
        assert sidecar.report()["tools"] == ["leaky", "ping"]
        assert sidecar.report()["middlewares"] == ["Marker"]

        assert asyncio.run(sidecar.unload("toolbox")) is True
        assert asyncio.run(sidecar.unload("leaky")) is True

        assert sidecar.report()["tools"] == []
        assert sidecar.report()["middlewares"] == []
        assert sidecar.report()["tool_owners"] == {}
    finally:
        sidecar.shutdown()


def test_unload_removes_routes_commands_and_services(bare) -> None:
    import asyncio

    bare.plugin("everything", '''
        from agentscope_sidecar import Context


        async def page(request):
            return {}


        async def setup(ctx: Context, routes, commands) -> None:
            routes.add("GET", "/page", page)
            commands.add("go", lambda args: None)
            ctx.provide("thing", 1)
    ''')
    sidecar = bare.boot()
    try:
        assert sidecar.report()["routes"] == {"GET /page": "everything"}
        assert sidecar.report()["commands"] == {"go": "everything"}

        assert asyncio.run(sidecar.unload("everything")) is True

        assert sidecar.report()["routes"] == {}
        assert sidecar.report()["commands"] == {}
        assert sidecar.ctx.get("thing") is None
    finally:
        sidecar.shutdown()


def test_unload_keeps_agent_overrides_in_report(bare) -> None:
    """``agent.*`` 构造参数覆盖回滚不了 —— 旧对象没被保留。

    所以报告里它继续挂着,这是**诚实**的:那个覆盖确实还在生效。
    """
    import asyncio

    from agentscope.agent import ReActConfig

    bare.plugin("tuner", '''
        from agentscope.agent import Agent, ReActConfig


        async def setup(agent: Agent) -> None:
            agent.react_config = ReActConfig(max_iters=20)
    ''')
    sidecar = bare.boot()
    try:
        assert asyncio.run(sidecar.unload("tuner")) is True
        assert sidecar.report()["agent_overrides"] == {"react_config": "tuner"}
        assert sidecar.build_agent().react_config.max_iters == 20
    finally:
        sidecar.shutdown()
    assert ReActConfig().max_iters != 20


def test_unload_clears_stale_unresolved(bare) -> None:
    """卸载后不该再报它的未解析依赖 —— 那条记录已经过期。"""
    import asyncio

    bare.plugin("needy", '''
        async def setup(never_provided) -> None:
            pass
    ''')
    sidecar = bare.boot()
    try:
        assert sidecar.report()["unresolved"] == ["never_provided"]
        asyncio.run(sidecar.unload("needy"))
        assert sidecar.report()["unresolved"] == []
    finally:
        sidecar.shutdown()


# ------------------------------------------------------------------ 夹具自身的约束


def test_fixture_plugin_ids_are_descriptive() -> None:
    """夹具也是一份真实项目目录,``inspect`` 报告会被人读。

    插件文件名就是插件 id —— ``policy.py`` 这类通用名放进夹具,报告里就分不出
    谁是谁,和 ``examples/`` 是同一个问题。
    """
    plugins = Path(__file__).parent / "agent" / "plugins"
    offenders = sorted(
        path.name for path in plugins.glob("*.py")
        if path.stem in GENERIC_PLUGIN_NAMES
    )
    assert not offenders, f"夹具里这些插件名太通用:{offenders}"


def test_fixture_plugin_ids_match_file_names(e2e) -> None:
    """报告里的 id 与文件名一致 —— 用户靠这个定位文件。"""
    sidecar = e2e.boot()
    try:
        for item in sidecar.report()["plugins"]:
            if item["order"] < 0:
                continue                                   # 内置插件不在夹具里
            assert Path(item["path"]).stem in {item["id"], "__init__"}
    finally:
        sidecar.shutdown()
