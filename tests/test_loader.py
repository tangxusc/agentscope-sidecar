"""加载器:时序、effect、快照差分、卸载、重载(设计文档 §3 / §8)。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from helpers import Project

from agentscope_sidecar.app import BUILTIN_PLUGINS_DIR
from agentscope_sidecar.loader import MIDDLEWARE_BUCKETS, PluginState, snapshot

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


async def setup(agent: Agent, ctx):
    await agent.toolkit.add_tool(FunctionTool({fn}))
    ctx.provide("provided_by_{name}", "{name}")
'''


def tool_plugin(name: str) -> str:
    return TOOL.format(fn=f"{name}_tool", name=name)


# ------------------------------------------------------------------ 激活


def test_plugin_without_setup_is_skipped(project: Project) -> None:
    project.plugin("empty", "x = 1\n")
    sidecar = project.boot()
    try:
        assert sidecar.loader.records["empty"].state is PluginState.SKIPPED
    finally:
        sidecar.shutdown()


def test_plugin_with_setup_is_loaded(project: Project) -> None:
    project.plugin("alpha", tool_plugin("alpha"))
    sidecar = project.boot()
    try:
        assert sidecar.loader.records["alpha"].state is PluginState.LOADED
    finally:
        sidecar.shutdown()


def test_agent_is_available_in_setup(project: Project) -> None:
    """时序反转的核心:``setup`` 里 agent 已经建好了。"""
    project.plugin("probe", '''
from agentscope.agent import Agent
from agentscope_sidecar import Context


async def setup(agent: Agent, ctx: Context):
    ctx.provide("probe", {
        "has_toolkit": agent.toolkit is not None,
        "has_model": agent.model is not None,
        "tool_count": len(agent.toolkit.tool_groups[0].tools),
    })
''')
    sidecar = project.boot()
    try:
        probe = sidecar.ctx.get("probe")
        assert probe["has_toolkit"] and probe["has_model"]
        assert probe["tool_count"] == 0         # 空壳起步
    finally:
        sidecar.shutdown()


def test_sync_setup_is_rejected(project: Project) -> None:
    project.plugin("sync", "def setup(agent):\n    pass\n")
    sidecar = project.boot()
    try:
        record = sidecar.loader.records["sync"]
        assert record.state is PluginState.FAILED
        assert "async def" in (record.error or "")
    finally:
        sidecar.shutdown()


def test_setup_returning_garbage_is_rejected(project: Project) -> None:
    project.plugin("bad", "async def setup():\n    return 42\n")
    sidecar = project.boot()
    try:
        record = sidecar.loader.records["bad"]
        assert record.state is PluginState.FAILED
        assert "effect" in (record.error or "")
    finally:
        sidecar.shutdown()


def test_setup_raising_is_isolated(project: Project) -> None:
    project.plugin("boom", 'async def setup():\n    raise ValueError("炸了")\n')
    project.plugin("ok", tool_plugin("ok"))
    sidecar = project.boot()
    try:
        assert sidecar.loader.records["boom"].state is PluginState.FAILED
        assert "炸了" in (sidecar.loader.records["boom"].error or "")
        assert sidecar.loader.records["ok"].state is PluginState.LOADED
    finally:
        sidecar.shutdown()


def test_import_error_is_isolated(project: Project) -> None:
    project.plugin("broken", "import no_such_module_xyz\n")
    project.plugin("ok", tool_plugin("ok"))
    sidecar = project.boot()
    try:
        assert sidecar.loader.records["broken"].state is PluginState.FAILED
        assert sidecar.loader.records["ok"].state is PluginState.LOADED
    finally:
        sidecar.shutdown()


# ------------------------------------------------------------------ effect


def record_plugin(project: Project, name: str, *, boom: bool = False) -> str:
    """生成一个把清理顺序写进文件的插件。

    不能用 ``_shared.py`` 传状态 —— ``_`` 前缀的条目会被移出插件集,import 不到。
    文件是唯一可靠的跨插件通道。
    """
    log = project.root / "order.txt"
    raise_line = '        raise RuntimeError("boom")\n' if boom else ""
    return f'''
from pathlib import Path

LOG = Path(r"{log}")


async def setup():
    def cleanup() -> None:
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write("{name}\\n")
{raise_line}    return cleanup
'''


def async_record_plugin(project: Project, name: str) -> str:
    log = project.root / "order.txt"
    return f'''
from pathlib import Path

LOG = Path(r"{log}")


async def setup():
    async def cleanup() -> None:
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write("{name}\\n")
    return cleanup
'''


def read_order(project: Project) -> list[str]:
    path = project.root / "order.txt"
    if not path.is_file():
        return []
    return path.read_text(encoding="utf-8").split()


# ------------------------------------------------------------------ effect


def test_effects_run_in_reverse_order(project: Project) -> None:
    for name in ("a", "b", "c"):
        project.plugin(name, record_plugin(project, name))
    sidecar = project.boot()
    try:
        sidecar.shutdown()
        assert read_order(project) == ["c", "b", "a"]
    except Exception:
        sidecar.shutdown()
        raise


def test_async_effect_is_awaited(project: Project) -> None:
    project.plugin("a", async_record_plugin(project, "a"))
    sidecar = project.boot()
    try:
        sidecar.shutdown()
        assert read_order(project) == ["a"]
    except Exception:
        sidecar.shutdown()
        raise


def test_failing_effect_does_not_block_others(project: Project) -> None:
    project.plugin("a", record_plugin(project, "a"))
    project.plugin("b", record_plugin(project, "b", boom=True))
    project.plugin("c", record_plugin(project, "c"))
    sidecar = project.boot()
    try:
        sidecar.shutdown()                      # 不该抛
        assert read_order(project) == ["c", "b", "a"]
    except Exception:
        sidecar.shutdown()
        raise


def test_none_effect_is_fine(project: Project) -> None:
    project.plugin("a", "async def setup():\n    return None\n")
    sidecar = project.boot()
    try:
        assert sidecar.loader.records["a"].state is PluginState.LOADED
        sidecar.shutdown()
    except Exception:
        sidecar.shutdown()
        raise


# ------------------------------------------------------------------ 归属差分


def test_snapshot_diff_attributes_tools(project: Project) -> None:
    project.plugin("alpha", tool_plugin("alpha"))
    project.plugin("beta", tool_plugin("beta"))
    sidecar = project.boot()
    try:
        assert sidecar.loader.records["alpha"].contributed["tools"] == ["alpha_tool"]
        assert sidecar.loader.records["beta"].contributed["tools"] == ["beta_tool"]
        owners = sidecar.report()["tool_owners"]
        assert owners["alpha_tool"] == "alpha"
        assert owners["beta_tool"] == "beta"
    finally:
        sidecar.shutdown()


def test_snapshot_diff_attributes_middlewares(project: Project) -> None:
    project.plugin("mw", '''
from agentscope.middleware import MiddlewareBase


class Tag(MiddlewareBase):
    async def on_model_call(self, agent, input_kwargs, next_handler):
        return await next_handler(**input_kwargs)


async def setup(agent):
    agent._model_call_middlewares.append(Tag())
''')
    sidecar = project.boot()
    try:
        assert sidecar.loader.records["mw"].contributed["middlewares"] == ["Tag"]
    finally:
        sidecar.shutdown()


def test_snapshot_diff_counts_same_type_twice(project: Project) -> None:
    """同类型多个中间件在报告里出现两次 —— 归属仍然正确。"""
    project.plugin("mw", '''
from agentscope.middleware import MiddlewareBase


class Tag(MiddlewareBase):
    async def on_model_call(self, agent, input_kwargs, next_handler):
        return await next_handler(**input_kwargs)


async def setup(agent):
    agent._model_call_middlewares.append(Tag())
    agent._model_call_middlewares.append(Tag())
''')
    sidecar = project.boot()
    try:
        assert sidecar.loader.records["mw"].contributed["middlewares"] == ["Tag", "Tag"]
    finally:
        sidecar.shutdown()


def test_snapshot_diff_attributes_agent_overrides(project: Project) -> None:
    project.plugin("policy", '''
from agentscope.agent import ContextConfig


async def setup(agent):
    agent.context_config = ContextConfig(trigger_ratio=0.5)
''')
    sidecar = project.boot()
    try:
        assert sidecar.loader.records["policy"].contributed["agent_overrides"] == [
            "context_config",
        ]
        assert sidecar.report()["agent_overrides"] == {"context_config": "policy"}
    finally:
        sidecar.shutdown()


def test_unresolved_dependencies_reported(project: Project) -> None:
    project.plugin("consumer", '''
async def setup(weather_service):
    pass
''')
    sidecar = project.boot()
    try:
        assert sidecar.loader.records["consumer"].unresolved == ["weather_service"]
        assert sidecar.report()["unresolved"] == ["weather_service"]
    finally:
        sidecar.shutdown()


def test_lazy_dependency_resolves_after_other_plugin(project: Project) -> None:
    """消费方先加载,提供方后加载 —— 只要**不在 setup 期间**用代理就没事。

    惰性代理是「首次使用才解析」。在 ``setup`` 里就碰它等于立刻解析,那时
    提供方还没跑,照样会 ``ServiceNotFound``。正确的用法是把它留到晚于全部
    setup 的时机(工具函数体、事件处理器)。
    """
    log = project.root / "seen.txt"
    project.plugin("a_consumer", f'''
from pathlib import Path

LOG = Path(r"{log}")


def on_ready(**payload: object) -> None:
    LOG.write_text(str(STORE["value"]), encoding="utf-8")


async def setup(store, events):
    global STORE
    STORE = store
    events.on("sidecar/ready", on_ready)
''')
    project.plugin("b_provider", '''
from agentscope_sidecar import Context


async def setup(ctx: Context):
    ctx.provide("store", {"value": 7})
''')
    sidecar = project.boot()
    try:
        assert (project.root / "seen.txt").read_text(encoding="utf-8") == "7"
    finally:
        sidecar.shutdown()


def test_lazy_proxy_used_during_setup_fails_fast(project: Project) -> None:
    """反面:在 ``setup`` 里就用代理 → 立刻解析 → 那时还没注册,报错。"""
    project.plugin("a_consumer", '''
async def setup(store):
    store["value"]                          # 立刻解析,提供方还没跑
''')
    project.plugin("b_provider", '''
from agentscope_sidecar import Context


async def setup(ctx: Context):
    ctx.provide("store", {"value": 7})
''')
    sidecar = project.boot()
    try:
        record = sidecar.loader.records["a_consumer"]
        assert record.state is PluginState.FAILED
        assert "store" in (record.error or "")
        assert sidecar.loader.records["b_provider"].state is PluginState.LOADED
    finally:
        sidecar.shutdown()


def test_snapshot_reports_nothing_for_clean_plugin(project: Project) -> None:
    project.plugin("clean", "async def setup():\n    pass\n")
    sidecar = project.boot()
    try:
        assert sidecar.loader.records["clean"].contributed == {}
    finally:
        sidecar.shutdown()


# ------------------------------------------------------------------ 服务与卸载


def test_plugin_provided_service_visible(project: Project) -> None:
    project.plugin("alpha", tool_plugin("alpha"))
    sidecar = project.boot()
    try:
        assert sidecar.ctx.get("provided_by_alpha") == "alpha"
    finally:
        sidecar.shutdown()


def test_unload_runs_effect_and_drops_services(project: Project) -> None:
    project.plugin("alpha", '''
from agentscope_sidecar import Context


async def setup(ctx: Context):
    ctx.provide("thing", 1)

    def cleanup() -> None:
        pass

    return cleanup
''')
    sidecar = project.boot()
    try:
        assert sidecar.ctx.has("thing")
        assert asyncio.run(sidecar.unload("alpha")) is True
        assert not sidecar.ctx.has("thing")
        assert sidecar.loader.records["alpha"].state is PluginState.UNLOADED
    finally:
        sidecar.shutdown()


def test_unload_unknown_plugin_returns_false(project: Project) -> None:
    sidecar = project.boot()
    try:
        assert asyncio.run(sidecar.unload("nope")) is False
    finally:
        sidecar.shutdown()


def test_unload_twice_returns_false(project: Project) -> None:
    project.plugin("alpha", "async def setup():\n    pass\n")
    sidecar = project.boot()
    try:
        assert asyncio.run(sidecar.unload("alpha")) is True
        assert asyncio.run(sidecar.unload("alpha")) is False
    finally:
        sidecar.shutdown()


# ------------------------------------------------------------------ 扫描与优先级


def test_underscore_prefixed_is_excluded(project: Project) -> None:
    project.plugin("_wip", "async def setup():\n    pass\n")
    project.plugin("keep", "async def setup():\n    pass\n")
    sidecar = project.boot()
    try:
        assert "keep" in sidecar.loader.records
        assert "_wip" not in sidecar.loader.records
    finally:
        sidecar.shutdown()


def test_later_dir_shadows_earlier(project: Project) -> None:
    base = project.plugin_dir("base")
    project.write_in(base, "same.py", tool_plugin("base"))
    override = project.plugin_dir("override")
    project.write_in(override, "same.py", tool_plugin("override"))

    sidecar = project.boot(plugins=(str(base), str(override)))
    try:
        record = sidecar.loader.records["same"]
        assert record.state is PluginState.LOADED
        assert record.root == override
        shadowed = sidecar.loader.shadowed_records
        assert len(shadowed) == 1
        assert shadowed[0].shadowed_by == override / "same.py"
    finally:
        sidecar.shutdown()


def test_builtin_plugins_are_lowest_priority(project: Project) -> None:
    """用户插件能覆盖内置的 ``skills`` / ``system_prompt``。"""
    project.plugin("system_prompt", "async def setup():\n    pass\n")
    sidecar = project.boot()
    try:
        record = sidecar.loader.records["system_prompt"]
        assert record.root == project.root / "plugins"
        assert not str(record.root).startswith(str(BUILTIN_PLUGINS_DIR))
    finally:
        sidecar.shutdown()


def test_extension_overrides_discovered_plugin(project: Project) -> None:
    """``-e`` 的文件按最高优先级并入,同 id 时把已发现的顶掉。"""
    project.plugin("same", tool_plugin("discovered"))
    extra_dir = project.plugin_dir("extra")
    project.write_in(extra_dir, "same.py", tool_plugin("from_extension"))

    sidecar = project.boot(extensions=(str(extra_dir / "same.py"),))
    try:
        record = sidecar.loader.records["same"]
        assert record.root == extra_dir
        assert record.contributed["tools"] == ["from_extension_tool"]
        assert len(sidecar.loader.shadowed_records) == 1
    finally:
        sidecar.shutdown()


# ------------------------------------------------------------------ 重载


def test_reload_rebuilds_agent_and_plugins(project: Project) -> None:
    project.plugin("alpha", tool_plugin("alpha"))
    sidecar = project.boot()
    try:
        before = sidecar.build_agent()
        asyncio.run(sidecar.reload())
        after = sidecar.build_agent()
        assert before is not after                   # Agent 必须重建
        assert [t.name for g in after.toolkit.tool_groups for t in g.tools] == [
            "alpha_tool",
        ]
    finally:
        sidecar.shutdown()


def test_reload_picks_up_new_plugin(project: Project) -> None:
    project.plugin("alpha", tool_plugin("alpha"))
    sidecar = project.boot()
    try:
        assert "beta" not in sidecar.loader.records
        project.plugin("beta", tool_plugin("beta"))
        asyncio.run(sidecar.reload())
        assert sidecar.loader.records["beta"].state is PluginState.LOADED
    finally:
        sidecar.shutdown()


def test_reload_drops_removed_plugin(project: Project) -> None:
    project.plugin("alpha", tool_plugin("alpha"))
    sidecar = project.boot()
    try:
        (project.root / "plugins" / "alpha.py").unlink()
        asyncio.run(sidecar.reload())
        assert "alpha" not in sidecar.loader.records
    finally:
        sidecar.shutdown()


# ------------------------------------------------------------------ snapshot 单元


def test_snapshot_counts_tools_and_middlewares(project: Project) -> None:
    project.plugin("alpha", tool_plugin("alpha"))
    sidecar = project.boot()
    try:
        snap = snapshot(sidecar.build_agent())
        assert snap.tools == {"alpha_tool": 1}
        assert set(snap.overrides) == {
            "context_config", "model_config", "injection_config",
            "react_config", "offloader",
        }
    finally:
        sidecar.shutdown()


def test_snapshot_diff_reports_additions_only(project: Project) -> None:
    project.plugin("alpha", tool_plugin("alpha"))
    sidecar = project.boot()
    try:
        agent = sidecar.build_agent()
        before = snapshot(agent)
        after = snapshot(agent)
        assert after.diff(before) == {}
        assert after.changed_overrides(before) == []
    finally:
        sidecar.shutdown()


def test_middleware_buckets_list_is_complete() -> None:
    """七个桶的名字与 AgentScope 的 hook 一一对应。"""
    from agentscope.agent import Agent
    from agentscope.tool import Toolkit
    from agentscope.model import ChatModelBase
    from agentscope.credential import CredentialBase
    from agentscope.formatter import OpenAIChatFormatter

    class C(CredentialBase):
        pass

    class M(ChatModelBase):
        def __init__(self) -> None:
            super().__init__(credential=C(), model="m", parameters=None, stream=True)
            self.formatter = OpenAIChatFormatter()

        async def __call__(self, *a, **k):    # pragma: no cover
            pass

    agent = Agent(name="t", system_prompt="", model=M(), toolkit=Toolkit(tools=[]))
    for bucket in MIDDLEWARE_BUCKETS:
        assert isinstance(getattr(agent, bucket), list)


# ------------------------------------------------------------------ 快照差分


class _FakeAgent:
    """只带快照要读的那几个字段。"""

    def __init__(self) -> None:
        self.toolkit = type("T", (), {"tool_groups": []})()
        for bucket in MIDDLEWARE_BUCKETS:
            setattr(self, bucket, [])
        for field in ("context_config", "model_config", "injection_config",
                      "react_config", "offloader"):
            setattr(self, field, None)


class _FakeTool:
    def __init__(self, name: str) -> None:
        self.name = name


def _agent_with(*tools: str, **overrides):
    agent = _FakeAgent()
    agent.toolkit.tool_groups = [type("G", (), {"tools": [_FakeTool(t) for t in tools]})()]
    for name, value in overrides.items():
        setattr(agent, name, value)
    return agent


def test_snapshot_counts_tools() -> None:
    snap = snapshot(_agent_with("a", "b"))
    assert snap.tools == {"a": 1, "b": 1}


def test_diff_reports_added_tools() -> None:
    before = snapshot(_agent_with("a"))
    after = snapshot(_agent_with("a", "b", "b"))
    assert after.diff(before) == {"tools": ["b", "b"]}


def test_diff_reports_removed_tools_as_nothing() -> None:
    """插件删掉工具时差值是负数 —— 不能变成「负数个名字」。"""
    before = snapshot(_agent_with("a", "b"))
    after = snapshot(_agent_with("a"))
    assert after.diff(before) == {}


def test_diff_reports_added_middlewares() -> None:
    class Marker:
        pass

    before = snapshot(_FakeAgent())
    agent = _FakeAgent()
    agent._model_call_middlewares.append(Marker())
    assert snapshot(agent).diff(before) == {"middlewares": ["Marker"]}


def test_snapshot_holds_override_objects_not_ids() -> None:
    """``overrides`` 存**对象本身**。

    只存 ``id()`` 的话旧对象可能被回收,新对象恰好复用同一个地址 —— 那时
    「换过对象」会被判成「没换」。存对象顺带把它钉住,地址不会被复用。
    """
    marker = object()
    agent = _agent_with(context_config=marker)
    snap = snapshot(agent)
    assert snap.overrides["context_config"] is marker


def test_changed_overrides_uses_identity() -> None:
    class Config:
        pass

    old = Config()
    before = snapshot(_agent_with(context_config=old))
    assert snapshot(_agent_with(context_config=old)).changed_overrides(before) == []
    assert snapshot(_agent_with(context_config=Config())).changed_overrides(before) == [
        "context_config",
    ]


def test_changed_overrides_ignores_fields_left_alone() -> None:
    before = snapshot(_FakeAgent())
    after = snapshot(_FakeAgent())
    assert after.changed_overrides(before) == []


def test_setting_override_to_none_is_not_a_change() -> None:
    """原本就是 ``None`` 的字段被赋 ``None`` —— 不算「改过」。"""
    before = snapshot(_FakeAgent())
    assert snapshot(_agent_with(offloader=None)).changed_overrides(before) == []


def test_clear_module_cache_by_prefix(project: Project) -> None:
    """按 ``sidecar_plugin_`` 前缀整段清 —— 目录形态的私有子模块也要清掉。"""
    import sys

    project.plugin("probe", "x = 1\n")
    sidecar = project.boot()
    try:
        assert any(k.startswith("sidecar_plugin_") for k in sys.modules)
        cleared = sidecar.loader.clear_module_cache()
        assert cleared
        assert not any(k.startswith("sidecar_plugin_") for k in sys.modules)
    finally:
        sidecar.shutdown()


def test_extension_with_invalid_name_is_skipped(project: Project) -> None:
    """``-e`` 绕过扫描的 ``_classify``,但名字校验不能跟着绕过。

    ``my-plugin`` 的模块键是 ``sidecar_plugin_my_plugin`` —— 正好和
    ``my_plugin`` 折叠成同一个,两个插件会共用一份模块对象。扫描路径严防这个,
    ``-e`` 也必须防。
    """
    extra = project.plugin_dir("extra")
    project.write_in(extra, "my-plugin.py", "async def setup():\n    pass\n")

    sidecar = project.boot(extensions=(str(extra / "my-plugin.py"),))
    try:
        assert "my-plugin" not in sidecar.loader.records
        warnings = sidecar.report()["warnings"]
        assert any("my-plugin" in w for w in warnings)
    finally:
        sidecar.shutdown()


def test_shadowed_record_keeps_plugin_dir_root(project: Project) -> None:
    """被覆盖的目录形态插件,``root`` 是插件目录 —— 否则报告里显示成内置。"""
    low = project.plugin_dir("low")
    high = project.plugin_dir("high")
    project.write_in(low, "audit/__init__.py", "async def setup():\n    pass\n")
    project.write_in(high, "audit/__init__.py", "async def setup():\n    pass\n")

    sidecar = project.boot(plugins=(str(low), str(high)))
    try:
        shadowed = sidecar.loader.shadowed_records
        assert len(shadowed) == 1
        assert shadowed[0].root == low
        item = next(p for p in sidecar.report()["plugins"] if p["state"] == "shadowed")
        assert item["order"] == 0                      # 不是 -1(那会被当成内置)
    finally:
        sidecar.shutdown()
