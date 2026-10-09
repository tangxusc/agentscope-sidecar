"""示例必须能跑。

文档里的示例最容易腐烂 —— 抄的人不知道它已过期。每个 ``examples/`` 下的目录都
在副本上真跑一遍,断言它确实做到了自己声称的事。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from helpers import GENERIC_PLUGIN_NAMES, Project, copy_tree, load_script

EXAMPLES = Path(__file__).parent.parent / "examples"

#: 目录名 -> 该示例里应当存在的插件 id。
CASES: dict[str, set[str]] = {
    "01-hello": {"greeting_tools"},
    "02-middleware": {"audit_trail"},
    "03-agent-config": {"compression_policy"},
    "04-routes": {"status_page"},
    "05-services": {"catalog_service", "catalog_tools"},
    "06-skills": {"skill_tools"},
}

@pytest.fixture
def example(tmp_path: Path):
    def build(name: str) -> Project:
        return Project(copy_tree(EXAMPLES / name, tmp_path / name))

    return build


def test_examples_directory_is_complete() -> None:
    """目录里不该有多余的示例 —— 测试没覆盖到的示例会腐烂。"""
    on_disk = {
        path.name for path in EXAMPLES.iterdir()
        if path.is_dir() and not path.name.startswith("_")
    }
    assert on_disk == set(CASES)


@pytest.mark.parametrize("name", sorted(CASES))
def test_example_plugin_ids_are_descriptive(name: str) -> None:
    """插件文件名就是插件 id,会出现在 ``inspect`` 报告里。"""
    for path in (EXAMPLES / name / "plugins").glob("*.py"):
        assert path.stem not in GENERIC_PLUGIN_NAMES, (
            f"{name}/plugins/{path.name} 名字太通用;它就是插件 id"
        )


@pytest.mark.parametrize("name", sorted(CASES))
def test_example_boots_without_failures(example, name: str) -> None:
    sidecar = example(name).boot()
    try:
        states = {
            item["id"]: item["state"] for item in sidecar.report()["plugins"]
        }
        for plugin_id in CASES[name]:
            assert states.get(plugin_id) == "loaded", (
                f"{name}:{plugin_id} 状态是 {states.get(plugin_id)}"
            )
    finally:
        sidecar.shutdown()


# ------------------------------------------------------------------ 逐个示例


def test_hello_example_registers_a_tool(example) -> None:
    sidecar = example("01-hello").boot()
    try:
        assert sidecar.report()["tool_owners"]["greet"] == "greeting_tools"
    finally:
        sidecar.shutdown()


def test_hello_example_runs_a_round(example) -> None:
    project = example("01-hello")
    result = project.cli(
        "打个招呼",
        env=project.env(MOCK_LLM_SCRIPT=load_script([
            {"tool_call": {"name": "greet", "args": {"name": "世界"}}},
            {"text": "打过了"},
        ])),
    )
    assert result.returncode == 0
    assert "打过了" in result.stdout


def test_hello_example_effect_cleans_up(example) -> None:
    project = example("01-hello")
    sidecar = project.boot()
    sidecar.shutdown()
    assert not (project.root / "calls.log").exists()


def test_middleware_example_appends_prompt_section(example) -> None:
    sidecar = example("02-middleware").boot()
    try:
        agent = sidecar.build_agent()
        assert [type(m).__name__ for m in agent._model_call_middlewares] == [
            "ModelCallAudit",
        ]
        assert "团队约定" in asyncio.run(agent._get_system_prompt())
    finally:
        sidecar.shutdown()


def test_middleware_example_middleware_actually_runs(example) -> None:
    """中间件真的被调用了 —— 用它的副作用验证,不是只看它挂上了。

    必须在**关闭之前**读日志:effect 会把它删掉。
    """
    from agentscope.message import UserMsg

    project = example("02-middleware")
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("MOCK_LLM_SCRIPT", load_script([{"text": "ok"}]))
    sidecar = project.boot()
    try:
        agent = sidecar.build_agent()
        asyncio.run(agent.reply(UserMsg(name="user", content="hi")))
        assert "model_call" in (project.root / "model_calls.log").read_text("utf-8")
    finally:
        sidecar.shutdown()
        monkeypatch.undo()


def test_agent_config_example_sets_fields(example) -> None:
    sidecar = example("03-agent-config").boot()
    try:
        agent = sidecar.build_agent()
        assert agent.context_config.trigger_ratio == 0.7
        assert agent.context_config.tool_result_limit == 20000
        assert agent.injection_config.timezone == "Asia/Shanghai"
        assert agent.react_config.max_iters == 20
        assert sidecar.report()["agent_overrides"]["context_config"] == (
            "compression_policy"
        )
    finally:
        sidecar.shutdown()


def test_agent_config_example_effect_restores_all_three_fields(example) -> None:
    """示例教的写法是「先留住原对象,effect 里放回去」。

    只还原其中一个字段的话,示例本身就是错的示范 —— 抄的人会以为卸载后配置
    会自动复原。
    """
    import asyncio

    sidecar = example("03-agent-config").boot()
    try:
        agent = sidecar.build_agent()
        original = (
            agent.context_config.trigger_ratio,
            agent.injection_config.timezone,
            agent.react_config.max_iters,
        )
        assert asyncio.run(sidecar.unload("compression_policy")) is True
        restored = (
            agent.context_config.trigger_ratio,
            agent.injection_config.timezone,
            agent.react_config.max_iters,
        )
        assert restored != original
        assert restored[0] != 0.7
        assert restored[1] != "Asia/Shanghai"
        assert restored[2] != 20
    finally:
        sidecar.shutdown()


def test_routes_example_registers_routes_and_command(example) -> None:
    sidecar = example("04-routes").boot()
    try:
        assert sidecar.report()["routes"] == {
            "GET /status": "status_page",
            "POST /echo": "status_page",
        }
        assert sidecar.commands.names() == ["status"]
    finally:
        sidecar.shutdown()


def test_routes_example_effect_unregisters(example) -> None:
    sidecar = example("04-routes").boot()
    asyncio.run(sidecar.unload("status_page"))
    assert sidecar.routes.describe() == {}
    assert sidecar.commands.names() == []
    sidecar.shutdown()


def test_services_example_provides_and_consumes(example) -> None:
    sidecar = example("05-services").boot()
    try:
        assert sidecar.ctx.get("catalog").items == ["apple", "banana"]
        assert sidecar.loader.records["catalog_tools"].unresolved == []
        assert sidecar.report()["tool_owners"]["list_items"] == "catalog_tools"
    finally:
        sidecar.shutdown()


def test_skills_example_reads_convention_paths(example) -> None:
    sidecar = example("06-skills").boot()
    try:
        agent = sidecar.build_agent()
        prompt = asyncio.run(agent._get_system_prompt())
        assert "# 示例 Agent" in prompt
        # 技能只把「名字 + 描述 + 目录」拼进提示词,正文等 Skill 工具去读
        assert "deploy" in prompt
        assert "部署一个服务的检查清单" in prompt
    finally:
        sidecar.shutdown()


def test_skills_example_skill_body_is_on_disk(example) -> None:
    project = example("06-skills")
    body = (project.root / "skills" / "deploy" / "SKILL.md").read_text("utf-8")
    assert "灰度发布" in body


def test_every_example_passes_inspect(example) -> None:
    """``inspect`` 是用户看插件的入口 —— 示例至少要让报告是干净的。"""
    for name in sorted(CASES):
        project = example(name)
        result = project.cli("inspect")
        assert result.returncode == 0, name
        assert "failed" not in result.stdout, name


def test_every_example_passes_check(example) -> None:
    for name in sorted(CASES):
        project = example(name)
        result = project.cli("check")
        assert result.returncode == 0, f"{name}: {result.stderr}"


def test_every_example_is_runnable_without_credentials(example) -> None:
    """示例不该要求 API 凭据 —— 没有凭据时回退到内置离线模型。"""
    for name in sorted(CASES):
        project = example(name)
        result = project.cli(
            "任务", env=project.env(MOCK_LLM_SCRIPT=load_script([{"text": "ok"}])),
        )
        assert result.returncode == 0, f"{name}: {result.stderr[-400:]}"
