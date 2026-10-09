"""事件与权限(设计文档 §6.6 / §11.8)。

事件用 ``lifecycle`` 插件的日志与总线计数验证;权限用真实工具调用验证 ——
被拦下的工具**不会执行**,只看状态位是不够的。
"""

from __future__ import annotations

import asyncio
import json

from agentscope.message import UserMsg

from helpers import load_script


def _log(e2e) -> str:
    return (e2e.root / "lifecycle.log").read_text(encoding="utf-8")


# ------------------------------------------------------------------ 事件


def test_plugin_loaded_event_per_plugin(e2e) -> None:
    sidecar = e2e.boot()
    try:
        count = _log(e2e).count("sidecar/plugin-loaded")
        # 13 个夹具插件,scratch 是 SKIPPED 不发 loaded;内置两个发
        assert count >= 12
    finally:
        sidecar.shutdown()


def test_plugin_failed_event_on_faulty(faulty) -> None:
    faulty.plugin("observer", '''
        from agentscope_sidecar import Context


        async def setup(ctx: Context, events) -> None:
            def record(**payload: object) -> None:
                seen = ctx.get("seen") or []
                ctx.provide("seen", [*seen, dict(payload)])

            events.on("sidecar/plugin-failed", record)
    ''')
    sidecar = faulty.boot()
    try:
        seen = sidecar.ctx.get("seen") or []
        assert len(seen) >= 5
        assert all(item["error"] for item in seen)
    finally:
        sidecar.shutdown()


def test_plugin_shadowed_event(shared, base_dir, biz_dir) -> None:
    shared.write(
        "observer.py",
        '''
        from pathlib import Path

        from agentscope_sidecar import Context

        LOG = Path(__file__).parent / "shadow.log"


        async def setup(ctx: Context, events) -> None:
            def record(**payload: object) -> None:
                LOG.write_text(
                    f"{payload['plugin']}|{payload['winner']}", encoding="utf-8",
                )

            events.on("sidecar/plugin-shadowed", record)
    ''',
    )
    sidecar = shared.boot(
        plugins=(str(base_dir), str(biz_dir)),
        extensions=(str(shared.root / "observer.py"),),
    )
    try:
        text = (shared.root / "shadow.log").read_text(encoding="utf-8")
        plugin_id, winner = text.split("|")
        assert plugin_id == "common"
        assert winner.endswith("common.py")
    finally:
        sidecar.shutdown()


def test_ready_event_counts_plugins(e2e) -> None:
    seen: dict[str, object] = {}

    e2e.plugin("ready_watcher", '''
        from agentscope_sidecar import Context


        async def setup(ctx: Context, events) -> None:
            def record(**payload: object) -> None:
                ctx.provide("ready_payload", dict(payload))

            events.on("sidecar/ready", record)
    ''')
    sidecar = e2e.boot()
    try:
        seen = sidecar.ctx.get("ready_payload")
        assert seen is not None
        assert seen["plugins"] >= 13
    finally:
        sidecar.shutdown()
    assert seen


def test_agent_event_is_forwarded(e2e, monkeypatch) -> None:
    """``forward_event`` 把 agent 循环事件转成 ``agent/event``。"""
    e2e.plugin("agent_watcher", '''
        from agentscope_sidecar import Context


        async def setup(ctx: Context, events) -> None:
            def record(**payload: object) -> None:
                seen = ctx.get("agent_events") or []
                ctx.provide("agent_events", [*seen, payload["type"]])

            events.on("agent/event", record)
    ''')
    monkeypatch.setenv("MOCK_LLM_SCRIPT", load_script([{"text": "回答"}]))
    sidecar = e2e.boot()
    try:
        agent = sidecar.build_agent()

        async def drive() -> None:
            async for event in agent.reply_stream(UserMsg(name="user", content="hi")):
                await sidecar.forward_event(event)

        asyncio.run(drive())
        seen = sidecar.ctx.get("agent_events") or []
        assert "model_call_start" in seen
        assert "reply_end" in seen
    finally:
        sidecar.shutdown()


def test_telemetry_counts_events(e2e) -> None:
    sidecar = e2e.boot()
    try:
        counters = sidecar.report()["telemetry"]["counters"]
        assert counters["sidecar/ready"] == 1
        assert counters["sidecar/plugin-loaded"] >= 12
    finally:
        sidecar.shutdown()


def test_telemetry_timeline_is_bounded(e2e) -> None:
    sidecar = e2e.boot()
    try:
        payload = sidecar.report()["telemetry"]
        assert 0 < payload["timeline_size"] <= 1000
        assert sidecar.telemetry.timeline
        assert {"event", "ts"} <= set(sidecar.telemetry.timeline[0])
    finally:
        sidecar.shutdown()


def test_telemetry_sink_writes_jsonl(e2e) -> None:
    from agentscope_sidecar import CliOptions, Sidecar
    from agentscope_sidecar.convention import resolve

    sink = e2e.root / "telemetry.jsonl"
    sidecar = Sidecar(
        resolve(CliOptions(), cwd=e2e.root), telemetry_sink=sink,
    )
    try:
        sidecar.boot()
    finally:
        sidecar.shutdown()
    lines = [json.loads(line) for line in sink.read_text(encoding="utf-8").splitlines()]
    assert any(item["event"] == "sidecar/ready" for item in lines)


def test_event_handler_error_does_not_break_others(bare) -> None:
    bare.plugin("a_bad", '''
        from agentscope_sidecar import Context


        async def setup(ctx: Context, events) -> None:
            def boom(**_: object) -> None:
                raise RuntimeError("订阅者炸了")

            events.on("sidecar/ready", boom)
    ''')
    bare.plugin("b_good", '''
        from pathlib import Path

        from agentscope_sidecar import Context

        LOG = Path(__file__).parent.parent / "good.txt"


        async def setup(ctx: Context, events) -> None:
            def ok(**_: object) -> None:
                LOG.write_text("到了", encoding="utf-8")

            events.on("sidecar/ready", ok)
    ''')
    sidecar = bare.boot()
    try:
        assert (bare.root / "good.txt").read_text(encoding="utf-8") == "到了"
    finally:
        sidecar.shutdown()


# ------------------------------------------------------------------ 权限


def test_permission_mode_from_flag(e2e) -> None:
    for mode in ("default", "accept_edits", "explore", "bypass", "dont_ask"):
        sidecar = e2e.boot(permission=mode)
        try:
            assert sidecar.build_agent().state.permission_context.mode.value == mode
        finally:
            sidecar.shutdown()


def _tool(agent, name: str):
    for group in agent.toolkit.tool_groups:
        for tool in group.tools:
            if tool.name == name:
                return tool
    raise AssertionError(f"夹具里没有工具 {name!r}")


def test_deny_rule_blocks_tool(e2e) -> None:
    """``permissions`` 插件给 ``wipe`` 加了 deny 规则。"""
    from agentscope.permission import PermissionEngine

    agent = e2e.boot().build_agent()
    engine = PermissionEngine(agent.state.permission_context)
    decision = asyncio.run(engine.check_permission(
        _tool(agent, "wipe"), {"path": "/tmp/x"},
    ))
    assert decision.behavior.value == "deny"


def test_deny_rule_wins_over_read_only(e2e) -> None:
    """deny 是第一步判定 —— 只读工具的快速放行也压不过它。"""
    from agentscope.permission import PermissionEngine

    agent = e2e.boot().build_agent()
    engine = PermissionEngine(agent.state.permission_context)
    decision = asyncio.run(engine.check_permission(
        _tool(agent, "wipe"), {"path": "anything"},
    ))
    assert decision.behavior.value == "deny"


def test_ask_rule_needs_confirmation(e2e) -> None:
    """ask 是第二步 —— 早于只读快速放行。"""
    from agentscope.permission import PermissionEngine

    agent = e2e.boot().build_agent()
    engine = PermissionEngine(agent.state.permission_context)
    decision = asyncio.run(engine.check_permission(
        _tool(agent, "make_report"), {"topic": "x"},
    ))
    assert decision.behavior.value == "ask"


def test_unlisted_tool_uses_mode_default(e2e) -> None:
    from agentscope.permission import PermissionEngine

    sidecar = e2e.boot(permission="bypass")
    try:
        agent = sidecar.build_agent()
        engine = PermissionEngine(agent.state.permission_context)
        decision = asyncio.run(engine.check_permission(
            _tool(agent, "get_weather"), {"city": "北京"},
        ))
        assert decision.behavior.value == "allow"
    finally:
        sidecar.shutdown()


def test_deny_rule_survives_bypass(e2e) -> None:
    """``bypass`` 下 ``deny`` 规则仍然生效 —— 那是它唯一保留的护栏。"""
    from agentscope.permission import PermissionEngine

    sidecar = e2e.boot(permission="bypass")
    try:
        agent = sidecar.build_agent()
        engine = PermissionEngine(agent.state.permission_context)
        decision = asyncio.run(engine.check_permission(
            _tool(agent, "wipe"), {"path": "/tmp/x"},
        ))
        assert decision.behavior.value == "deny"
    finally:
        sidecar.shutdown()


def test_ask_blocks_execution_in_non_interactive(e2e) -> None:
    """非交互模式下需要确认 —— 工具**不会执行**。

    ``write_note`` 没有任何权限规则,所以 ``default`` 模式下走 ASK。判断依据是
    工具自己的副作用(``notes.log``),不是 tool result —— 被拒绝的调用同样会产生
    tool result。
    """
    script = load_script([
        {"tool_call": {"name": "write_note", "args": {"text": "季度"}}},
        {"text": "结束"},
    ])
    result = e2e.cli(
        "--mode", "json", "记一条笔记", env=e2e.env(MOCK_LLM_SCRIPT=script),
    )
    assert result.returncode == 0
    assert not (e2e.root / "notes.log").exists()
    assert "无法交互" in result.stderr
    # 无人应答的 ASK 会让整轮**停在确认**上 —— 没有 tool_result,没有后续文本
    types = [
        json.loads(line)["type"]
        for line in result.stdout.splitlines() if line.strip()
    ]
    assert "require_user_confirm" in types
    assert "tool_result_end" not in types


def test_bypass_allows_execution(e2e) -> None:
    """``bypass`` 下同一个调用会真的执行 —— 对照组。"""
    script = load_script([
        {"tool_call": {"name": "write_note", "args": {"text": "季度"}}},
        {"text": "结束"},
    ])
    result = e2e.cli(
        "--mode", "json", "--permission", "bypass", "记一条笔记",
        env=e2e.env(MOCK_LLM_SCRIPT=script),
    )
    assert result.returncode == 0
    assert (e2e.root / "notes.log").read_text(encoding="utf-8") == "季度\n"


def test_deny_rule_blocks_even_with_bypass(e2e) -> None:
    """``bypass`` 保留的唯一护栏是 ``deny`` 规则。"""
    script = load_script([
        {"tool_call": {"name": "wipe", "args": {"path": "/tmp/x"}}},
        {"text": "结束"},
    ])
    result = e2e.cli(
        "--mode", "json", "--permission", "bypass", "删掉",
        env=e2e.env(MOCK_LLM_SCRIPT=script),
    )
    assert result.returncode == 0
    assert not (e2e.root / "wipe.log").exists()


def test_non_interactive_confirm_logs_warning(e2e, caplog) -> None:
    """非交互模式下需要确认 —— 工具不执行,必须告警。"""
    import logging
    from types import SimpleNamespace

    event = SimpleNamespace(
        type="REQUIRE_USER_CONFIRM",
        tool_calls=[SimpleNamespace(name="wipe")],
    )

    sidecar = e2e.boot(mode="json")
    try:
        with caplog.at_level(logging.WARNING, logger="sidecar"):
            asyncio.run(sidecar.forward_event(event))
        assert any("无法交互" in record.getMessage() for record in caplog.records)
    finally:
        sidecar.shutdown()


def test_interactive_confirm_does_not_warn(e2e, caplog) -> None:
    import logging
    from types import SimpleNamespace

    event = SimpleNamespace(type="REQUIRE_USER_CONFIRM", tool_calls=[])

    sidecar = e2e.boot(mode="console")
    try:
        with caplog.at_level(logging.WARNING, logger="sidecar"):
            asyncio.run(sidecar.forward_event(event))
        assert not any(
            "无法交互" in record.getMessage() for record in caplog.records
        )
    finally:
        sidecar.shutdown()


# ------------------------------------------------------------------ 过滤


def test_tools_filter_keeps_only_named(e2e) -> None:
    sidecar = e2e.boot(tools=("get_weather", "list_cities"))
    try:
        assert sidecar.report()["tools"] == ["get_weather", "list_cities"]
    finally:
        sidecar.shutdown()


def test_tools_filter_applies_after_setup(e2e) -> None:
    """过滤在所有 setup 之后 —— 否则插件还没挂上工具,过滤是空操作。"""
    sidecar = e2e.boot(tools=("wait",))
    try:
        assert sidecar.report()["tools"] == ["wait"]
        assert sidecar.loader.records["weather"].state.value == "loaded"
    finally:
        sidecar.shutdown()


def test_tools_filter_empty_removes_all(e2e) -> None:
    sidecar = e2e.boot(tools=())
    try:
        assert sidecar.report()["tools"] == []
    finally:
        sidecar.shutdown()


def test_middlewares_filter(e2e) -> None:
    sidecar = e2e.boot(middlewares=("AuditTrail",))
    try:
        assert sidecar.report()["middlewares"] == ["AuditTrail"]
    finally:
        sidecar.shutdown()


def test_no_filter_keeps_everything(e2e) -> None:
    sidecar = e2e.boot()
    try:
        assert len(sidecar.report()["tools"]) == 7
        assert len(sidecar.report()["middlewares"]) == 3
    finally:
        sidecar.shutdown()


def test_tool_owners_only_lists_present_tools(e2e) -> None:
    """``--tools`` 过滤后,``tool_owners`` 不能还列着被滤掉的工具。

    报告要描述**当前状态** —— 否则 ``tools`` 说只有 1 个,``tool_owners`` 说
    有 7 个,两份数据自相矛盾。
    """
    sidecar = e2e.boot(tools=("get_weather",))
    try:
        report = sidecar.report()
        assert report["tools"] == ["get_weather"]
        assert report["tool_owners"] == {"get_weather": "weather"}
    finally:
        sidecar.shutdown()


def test_plugin_record_still_remembers_filtered_tools(e2e) -> None:
    """过滤只是从 Agent 上摘掉 —— 插件记录里的归属信息不丢。"""
    sidecar = e2e.boot(tools=("get_weather",))
    try:
        record = sidecar.loader.records["weather"]
        assert sorted(record.contributed["tools"]) == ["get_weather", "list_cities"]
    finally:
        sidecar.shutdown()
