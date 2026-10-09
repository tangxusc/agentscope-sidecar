"""Agent 装配:工具、中间件、提示词、技能、构造参数(设计文档 §4 / §7)。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from expected import LIFECYCLE_EVENTS, TUNING_OVERRIDES


def _prompt(sidecar) -> str:
    return asyncio.run(sidecar.build_agent()._get_system_prompt())


def _skills(sidecar) -> str:
    return asyncio.run(sidecar.build_agent().toolkit.get_skill_instructions()) or ""


# ------------------------------------------------------------------ 系统提示词


def test_system_md_is_injected(e2e) -> None:
    """内置 ``system_prompt`` 插件读 ``./SYSTEM.md``。"""
    sidecar = e2e.boot()
    try:
        text = _prompt(sidecar)
        assert "# 示例 Agent" in text
        assert "回答要简短" in text
    finally:
        sidecar.shutdown()


def test_system_md_plugin_is_attributed(e2e) -> None:
    sidecar = e2e.boot()
    try:
        assert sidecar.loader.records["system_prompt"].contributed["middlewares"] == [
            "SystemFileSection",
        ]
    finally:
        sidecar.shutdown()


def test_plugin_prompt_section_appends_after_file(e2e) -> None:
    """文件在前,插件片段在后 —— 中间件是追加,不是覆盖。"""
    sidecar = e2e.boot()
    try:
        text = _prompt(sidecar)
        assert text.index("# 示例 Agent") < text.index("# 审计")
    finally:
        sidecar.shutdown()


def test_missing_system_md_is_not_an_error(bare) -> None:
    sidecar = bare.boot()
    try:
        assert _prompt(sidecar).strip() == ""
        assert sidecar.loader.records["system_prompt"].state.value == "loaded"
    finally:
        sidecar.shutdown()


def test_inline_system_overrides_file(e2e) -> None:
    """``--system`` 内联文本优先于 ``SYSTEM.md`` —— 是二选一,不是拼接。"""
    sidecar = e2e.boot(system="内联提示词")
    try:
        text = _prompt(sidecar)
        assert "内联提示词" in text
        assert "# 示例 Agent" not in text
    finally:
        sidecar.shutdown()


def test_system_file_override(e2e) -> None:
    e2e.write("other.md", "# 另一个提示词\n")
    sidecar = e2e.boot(system_file="./other.md")
    try:
        text = _prompt(sidecar)
        assert "# 另一个提示词" in text
        assert "# 示例 Agent" not in text
    finally:
        sidecar.shutdown()


def test_custom_system_file_missing_is_silent(e2e) -> None:
    """显式指定的文件不存在 —— 什么都不做,也不回退到 ``./SYSTEM.md``。"""
    sidecar = e2e.boot(system_file="./nope.md")
    try:
        text = _prompt(sidecar)
        assert "# 示例 Agent" not in text
        assert "# 审计" in text                  # 插件片段不受影响
    finally:
        sidecar.shutdown()


# ------------------------------------------------------------------ 技能


def test_skills_directory_is_registered(e2e) -> None:
    sidecar = e2e.boot()
    try:
        text = _skills(sidecar)
        assert "code-review" in text
        assert "代码审查清单" in text
    finally:
        sidecar.shutdown()


def test_skill_instructions_appear_in_system_prompt(e2e) -> None:
    sidecar = e2e.boot()
    try:
        assert "<agent-skills>" in _prompt(sidecar)
    finally:
        sidecar.shutdown()


def test_missing_skills_directory_is_not_an_error(bare) -> None:
    sidecar = bare.boot()
    try:
        assert _skills(sidecar) == ""
    finally:
        sidecar.shutdown()


def test_skills_flag_replaces_default(e2e) -> None:
    """``--skills`` 是替换,不是追加。"""
    e2e.write(
        "other-skills/deploy/SKILL.md",
        "---\nname: deploy\ndescription: 部署\n---\n\n# 部署\n",
    )
    sidecar = e2e.boot(skills=(str(e2e.root / "other-skills"),))
    try:
        text = _skills(sidecar)
        assert "deploy" in text
        assert "code-review" not in text
    finally:
        sidecar.shutdown()


def test_skills_flag_accepts_a_bare_string(e2e) -> None:
    """传单个字符串要当成一条路径 —— 不是逐字符拆开。

    ``list("./skills")`` 是 ``['.', '/', 's', ...]``,其中 ``"/"`` 恰好存在,
    于是技能加载器会去遍历整个文件系统。
    """
    from agentscope_sidecar import CliOptions
    from agentscope_sidecar.convention import resolve

    options = resolve(CliOptions(skills="/tmp/one-dir"), cwd=e2e.root)
    assert options.skills_dir == Path("/tmp/one-dir")
    assert options.extra_skills == ()


def test_skills_flag_is_repeatable(e2e) -> None:
    extra = e2e.root / "extra-skills"
    e2e.write(
        "extra-skills/deploy/SKILL.md",
        "---\nname: deploy\ndescription: 部署\n---\n\n# 部署\n",
    )
    sidecar = e2e.boot(skills=(str(e2e.root / "skills"), str(extra)))
    try:
        text = _skills(sidecar)
        assert "code-review" in text
        assert "deploy" in text
    finally:
        sidecar.shutdown()


# ------------------------------------------------------------------ 中间件


def test_middleware_buckets_are_populated(e2e) -> None:
    sidecar = e2e.boot()
    try:
        agent = sidecar.build_agent()
        assert [type(m).__name__ for m in agent._model_call_middlewares] == [
            "AuditTrail",
        ]
        assert [type(m).__name__ for m in agent._system_prompt_middlewares] == [
            "SystemFileSection", "PromptSection",
        ]
    finally:
        sidecar.shutdown()


def test_middleware_names_in_report(e2e) -> None:
    sidecar = e2e.boot()
    try:
        assert sidecar.report()["middlewares"] == [
            "AuditTrail", "PromptSection", "SystemFileSection",
        ]
    finally:
        sidecar.shutdown()


# ------------------------------------------------------------------ 构造参数


def test_plugin_can_override_agent_config(e2e) -> None:
    agent = e2e.boot().build_agent()
    assert agent.context_config.trigger_ratio == 0.7
    assert agent.context_config.tool_result_limit == 20000
    assert agent.injection_config.timezone == "Asia/Shanghai"
    assert agent.react_config.max_iters == 20
    assert agent.react_config.stop_on_reject is True


def test_overrides_are_all_from_agent_tuning(e2e) -> None:
    sidecar = e2e.boot()
    try:
        overrides = sidecar.report()["agent_overrides"]
        assert sorted(overrides) == sorted(TUNING_OVERRIDES)
        assert set(overrides.values()) == {"agent_tuning"}
    finally:
        sidecar.shutdown()


def test_unset_fields_keep_agentscope_defaults(e2e) -> None:
    """插件没碰的字段仍是 AgentScope 默认值 —— 不做字段级合并。"""
    agent = e2e.boot().build_agent()
    assert agent.model_config.max_retries >= 0
    assert agent.offloader is None


# ------------------------------------------------------------------ 权限


def test_plugin_can_add_permission_rules(e2e) -> None:
    agent = e2e.boot().build_agent()
    context = agent.state.permission_context
    assert [r.tool_name for r in context.deny_rules["wipe"]] == ["wipe"]
    assert [r.tool_name for r in context.ask_rules["make_report"]] == ["make_report"]
    assert context.deny_rules["wipe"][0].source == "permissions"


def test_permission_mode_comes_from_options(e2e) -> None:
    sidecar = e2e.boot(permission="accept_edits")
    try:
        context = sidecar.build_agent().state.permission_context
        assert context.mode.value == "accept_edits"
    finally:
        sidecar.shutdown()


# ------------------------------------------------------------------ 事件订阅


def test_lifecycle_subscribes_all_events(e2e) -> None:
    sidecar = e2e.boot()
    try:
        assert sidecar.bus.total_listeners() >= LIFECYCLE_EVENTS
        # ``sidecar/ready`` 有 telemetry 也订阅着,所以是 2 不是 1
        assert sidecar.bus.listener_count("sidecar/ready") >= 1
        assert sidecar.bus.listener_count("sidecar/plugin-loaded") >= 1
    finally:
        sidecar.shutdown()


def test_lifecycle_log_records_lifecycle(e2e) -> None:
    sidecar = e2e.boot()
    try:
        log = (e2e.root / "lifecycle.log").read_text(encoding="utf-8")
        assert "sidecar/plugin-loaded" in log
        assert "sidecar/ready" in log
    finally:
        sidecar.shutdown()


def test_boot_event_is_not_observable_by_plugins(e2e) -> None:
    """``sidecar/boot`` 在插件加载**之前**发 —— 插件订阅时它已经过去了。

    这不是缺陷:插件能观察的起点是 ``plugin-loaded``。夹具里订阅了它,但收不到,
    这条测试就是把这个事实钉住。
    """
    sidecar = e2e.boot()
    try:
        log = (e2e.root / "lifecycle.log").read_text(encoding="utf-8")
        assert "sidecar/boot" not in log
    finally:
        sidecar.shutdown()


def test_shutdown_event_arrives_before_effects(e2e) -> None:
    """关闭顺序:先广播 ``sidecar/shutdown``,再逆序调 effect。

    反过来的话,effect 删掉日志文件之后那条广播又会被残留订阅者写一遍。
    """
    sidecar = e2e.boot()
    sidecar.shutdown()
    assert not (e2e.root / "lifecycle.log").exists()


# ------------------------------------------------------------------ 服务


def test_service_is_shared_across_plugins(e2e) -> None:
    """``catalog`` 提供服务,``consumer`` 按名称注入。"""
    sidecar = e2e.boot()
    try:
        assert sidecar.ctx.get("catalog").items == ["a", "b"]
    finally:
        sidecar.shutdown()


def test_service_removed_on_unload(e2e) -> None:
    sidecar = e2e.boot()
    try:
        assert asyncio.run(sidecar.unload("catalog")) is True
        assert sidecar.ctx.get("catalog") is None
    finally:
        sidecar.shutdown()


@pytest.mark.parametrize("mode", ["once", "console", "json", "rpc", "http"])
def test_ctx_interactive_flag(bare, mode: str) -> None:
    """``ctx.interactive`` 只在 console 为 True。"""
    bare.plugin("probe", '''
        from pathlib import Path

        from agentscope_sidecar import Context

        LOG = Path(__file__).parent.parent / "interactive.txt"


        async def setup(ctx: Context) -> None:
            LOG.write_text(str(ctx.interactive), encoding="utf-8")
    ''')
    sidecar = bare.boot(mode=mode)
    try:
        text = (bare.root / "interactive.txt").read_text(encoding="utf-8")
        assert text == str(mode == "console")
    finally:
        sidecar.shutdown()
