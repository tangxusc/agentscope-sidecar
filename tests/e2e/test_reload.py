"""热重载(设计文档 §11.9)。

重载 = 逆序调全部 effect → 清模块缓存 → 重建 Agent → 重新扫描与激活。
**Agent 必须重建** —— 它持有旧插件贡献的中间件与工具实例。
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import time

from agentscope_sidecar import PluginState
from agentscope_sidecar.reload import PollingWatcher, diff, snapshot


def _tool_plugin(tool: str, text: str = "v1") -> str:
    return f'''
from pathlib import Path

from agentscope.agent import Agent
from agentscope.message import TextBlock
from agentscope.tool import FunctionTool, ToolResponse

LOG = Path(__file__).parent.parent / "version.txt"


async def {tool}() -> ToolResponse:
    """返回版本号。"""
    return ToolResponse(content=[TextBlock(type="text", text="{text}")])


async def setup(agent: Agent) -> None:
    await agent.toolkit.add_tool(FunctionTool({tool}, is_read_only=True))
'''


# ------------------------------------------------------------------ 进程内


def test_reload_reruns_setup(bare) -> None:
    bare.plugin("probe", '''
        from pathlib import Path

        from agentscope_sidecar import Context

        LOG = Path(__file__).parent.parent / "runs.txt"


        async def setup(ctx: Context) -> None:
            with LOG.open("a", encoding="utf-8") as handle:
                handle.write("x")
    ''')
    sidecar = bare.boot()
    try:
        assert (bare.root / "runs.txt").read_text(encoding="utf-8") == "x"
        asyncio.run(sidecar.reload())
        assert (bare.root / "runs.txt").read_text(encoding="utf-8") == "xx"
    finally:
        sidecar.shutdown()


def test_reload_runs_old_effects(bare) -> None:
    bare.plugin("probe", '''
        from pathlib import Path

        from agentscope_sidecar import Effect

        LOG = Path(__file__).parent.parent / "effects.txt"


        async def setup() -> Effect:
            def cleanup() -> None:
                with LOG.open("a", encoding="utf-8") as handle:
                    handle.write("c")

            return cleanup
    ''')
    sidecar = bare.boot()
    try:
        asyncio.run(sidecar.reload())
        assert (bare.root / "effects.txt").read_text(encoding="utf-8") == "c"
    finally:
        sidecar.shutdown()


def test_reload_picks_up_source_changes(bare) -> None:
    """改了插件源码要生效 —— 靠清 ``sys.modules`` 里 ``sidecar_plugin_`` 前缀。"""
    bare.plugin("greeter", '''
        from pathlib import Path

        from agentscope_sidecar import Context

        LOG = Path(__file__).parent.parent / "greet.txt"


        async def setup(ctx: Context) -> None:
            LOG.write_text("v1", encoding="utf-8")
    ''')
    sidecar = bare.boot()
    try:
        assert (bare.root / "greet.txt").read_text(encoding="utf-8") == "v1"
        bare.plugin("greeter", '''
            from pathlib import Path

            from agentscope_sidecar import Context

            LOG = Path(__file__).parent.parent / "greet.txt"


            async def setup(ctx: Context) -> None:
                LOG.write_text("v2", encoding="utf-8")
        ''')
        asyncio.run(sidecar.reload())
        assert (bare.root / "greet.txt").read_text(encoding="utf-8") == "v2"
    finally:
        sidecar.shutdown()


def test_reload_rebuilds_agent(bare) -> None:
    """Agent 对象必须换新 —— 旧的那个还挂着旧插件的中间件实例。"""
    bare.plugin("mw", '''
        from agentscope.agent import Agent
        from agentscope.middleware import MiddlewareBase


        class Marker(MiddlewareBase):
            async def on_model_call(self, agent: Agent, input_kwargs, next_handler):
                return await next_handler(**input_kwargs)


        async def setup(agent: Agent) -> None:
            agent._model_call_middlewares.append(Marker())
    ''')
    sidecar = bare.boot()
    try:
        first = sidecar.build_agent()
        asyncio.run(sidecar.reload())
        second = sidecar.build_agent()
        assert first is not second
        assert len(second._model_call_middlewares) == 1
        assert first is not second
    finally:
        sidecar.shutdown()


def test_reload_does_not_duplicate_tools(bare) -> None:
    bare.plugin("tool_holder", _tool_plugin("version"))
    sidecar = bare.boot()
    try:
        assert sidecar.report()["tools"] == ["version"]
        asyncio.run(sidecar.reload())
        assert sidecar.report()["tools"] == ["version"]
    finally:
        sidecar.shutdown()


def test_reload_emits_event(bare) -> None:
    bare.plugin("watcher", '''
        from agentscope_sidecar import Context


        async def setup(ctx: Context, events) -> None:
            def record(**payload: object) -> None:
                ctx.provide("reload_payload", dict(payload))

            events.on("sidecar/reloaded", record)
    ''')
    sidecar = bare.boot()
    try:
        asyncio.run(sidecar.reload())
        payload = sidecar.ctx.get("reload_payload")
        assert payload is not None
        assert payload["plugins"] >= 1
    finally:
        sidecar.shutdown()


def test_reload_adds_new_plugin(bare) -> None:
    sidecar = bare.boot()
    try:
        assert sidecar.report()["tools"] == []
        bare.plugin("tool_holder", _tool_plugin("version"))
        asyncio.run(sidecar.reload())
        assert sidecar.report()["tools"] == ["version"]
    finally:
        sidecar.shutdown()


def test_reload_removes_deleted_plugin(bare) -> None:
    bare.plugin("tool_holder", _tool_plugin("version"))
    sidecar = bare.boot()
    try:
        assert sidecar.report()["tools"] == ["version"]
        (bare.root / "plugins" / "tool_holder.py").unlink()
        asyncio.run(sidecar.reload())
        assert sidecar.report()["tools"] == []
    finally:
        sidecar.shutdown()


def test_reload_clears_shadowed_records(bare) -> None:
    other = bare.plugin_dir("other")
    bare.write_in(other, "same.py", _tool_plugin("version", "other"))
    bare.plugin("same", _tool_plugin("version", "main"))

    sidecar = bare.boot(plugins=(str(other),))
    try:
        assert len(sidecar.loader.shadowed_records) == 1
        asyncio.run(sidecar.reload())
        assert len(sidecar.loader.shadowed_records) == 1      # 不是 2
    finally:
        sidecar.shutdown()


def test_reload_keeps_agent_overrides_attribution(e2e) -> None:
    sidecar = e2e.boot()
    try:
        assert sidecar.report()["agent_overrides"]["react_config"] == "agent_tuning"
        asyncio.run(sidecar.reload())
        assert sidecar.report()["agent_overrides"]["react_config"] == "agent_tuning"
        assert sidecar.loader.records["agent_tuning"].state is PluginState.LOADED
    finally:
        sidecar.shutdown()


def test_reload_reapplies_filters(e2e) -> None:
    sidecar = e2e.boot(tools=("get_weather",))
    try:
        assert sidecar.report()["tools"] == ["get_weather"]
        asyncio.run(sidecar.reload())
        assert sidecar.report()["tools"] == ["get_weather"]
    finally:
        sidecar.shutdown()


# ------------------------------------------------------------------ 监听器


def test_polling_watcher_detects_new_file(bare) -> None:
    seen: list[list[str]] = []

    async def main() -> None:
        watcher = PollingWatcher([bare.root / "plugins"], interval=0.05)

        async def on_change(changes: list[str]) -> None:
            seen.append(changes)

        task = asyncio.ensure_future(watcher.run(on_change))
        await asyncio.sleep(0.15)
        bare.plugin("late", "x = 1\n")
        await asyncio.sleep(0.4)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(main())
    assert seen
    assert any("late.py" in item for item in seen[0])


def test_snapshot_diff_reports_add_remove_modify(bare) -> None:
    plugins = bare.root / "plugins"
    bare.plugin("a", "x = 1\n")
    before = snapshot([plugins])

    bare.plugin("b", "y = 2\n")
    (plugins / "a.py").write_text("x = 2\n", encoding="utf-8")
    after = snapshot([plugins])

    changes = diff(before, after)
    assert any("b.py" in c for c in changes)
    assert any("a.py" in c for c in changes)

    (plugins / "a.py").unlink()
    assert any("a.py" in c for c in diff(after, snapshot([plugins])))


def test_snapshot_ignores_pycache_and_hidden(bare) -> None:
    plugins = bare.root / "plugins"
    (plugins / "__pycache__").mkdir()
    (plugins / "__pycache__" / "x.pyc").write_text("x", encoding="utf-8")
    (plugins / ".hidden").write_text("x", encoding="utf-8")
    assert snapshot([plugins]) == {}


def test_watchfiles_available_is_a_bool() -> None:
    from agentscope_sidecar.reload import watchfiles_available

    assert isinstance(watchfiles_available(), bool)


# ------------------------------------------------------------------ --reload


def test_reload_flag_starts_watcher(bare) -> None:
    """``--reload`` 下改插件源码,不用重启就能生效。"""
    bare.plugin("greeter", '''
        from pathlib import Path

        from agentscope_sidecar import Context

        LOG = Path(__file__).parent.parent / "greet.txt"


        async def setup(ctx: Context) -> None:
            LOG.write_text("v1", encoding="utf-8")
    ''')
    from helpers import load_script

    proc = subprocess.Popen(
        [sys.executable, "-m", "agentscope_sidecar", "--mode", "rpc", "--reload"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env=bare.env(MOCK_LLM_SCRIPT=load_script([{"text": "ok"}])),
        cwd=bare.root,
    )
    try:
        assert _wait_for(bare.root / "greet.txt", "v1"), proc.stderr.read()[-500:]
        bare.plugin("greeter", '''
            from pathlib import Path

            from agentscope_sidecar import Context

            LOG = Path(__file__).parent.parent / "greet.txt"


            async def setup(ctx: Context) -> None:
                LOG.write_text("v2", encoding="utf-8")
        ''')
        assert _wait_for(bare.root / "greet.txt", "v2", timeout=20), (
            "改了源码但没重载:" + proc.stderr.read()[-500:]
        )
    finally:
        proc.terminate()
        proc.wait(timeout=15)


def _wait_for(path, text: str, *, timeout: float = 15) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if path.read_text(encoding="utf-8") == text:
                return True
        except FileNotFoundError:
            pass
        time.sleep(0.2)
    return False
