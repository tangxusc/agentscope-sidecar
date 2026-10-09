"""CLI 子进程(设计文档 §11)。"""

from __future__ import annotations

import json
import subprocess
import sys

from helpers import Project, load_script

TOOL = '''
from agentscope.agent import Agent
from agentscope.tool import FunctionTool, ToolResponse
from agentscope.message import TextBlock


async def hello(name: str) -> ToolResponse:
    """打个招呼。

    Args:
        name (str): 名字
    """
    return ToolResponse(content=[TextBlock(type="text", text=f"你好 {name}")])


async def setup(agent: Agent):
    # 只读工具在 default 权限模式下不需要确认 —— 非交互模式才能直接跑
    await agent.toolkit.add_tool(FunctionTool(hello, is_read_only=True))
'''


def test_run_once_prints_answer(project: Project) -> None:
    project.plugin("hello", TOOL)
    env = project.env(MOCK_LLM_SCRIPT=load_script([
        {"tool_call": {"name": "hello", "args": {"name": "世界"}}},
        {"text": "完成"},
    ]))
    result = project.cli("任务", env=env)
    assert result.returncode == 0
    assert "完成" in result.stdout


def test_run_without_task_in_once_mode_fails(project: Project) -> None:
    result = project.cli("--mode", "once")
    assert result.returncode == 2
    assert "需要一个任务参数" in result.stderr


def test_inspect_text_report(project: Project) -> None:
    project.plugin("hello", TOOL)
    result = project.cli("inspect")
    assert result.returncode == 0
    assert "hello" in result.stdout
    assert "工具:hello" in result.stdout


def test_inspect_json_report(project: Project) -> None:
    project.plugin("hello", TOOL)
    result = project.cli("inspect", "--json")
    assert result.returncode == 0
    report = json.loads(result.stdout)
    assert report["tools"] == ["hello"]
    assert report["tool_owners"] == {"hello": "hello"}


def test_list_shows_states(project: Project) -> None:
    project.plugin("hello", TOOL)
    project.plugin("empty", "x = 1\n")
    result = project.cli("list")
    assert result.returncode == 0
    lines = {
        name: state
        for state, name in (line.split() for line in result.stdout.strip().splitlines())
    }
    assert lines["hello"] == "loaded"
    assert lines["empty"] == "skipped"


def test_check_passes_on_healthy(project: Project) -> None:
    project.plugin("hello", TOOL)
    result = project.cli("check")
    assert result.returncode == 0
    assert "0 个插件失败" in result.stdout


def test_check_fails_on_broken_plugin(project: Project) -> None:
    project.plugin("boom", "async def setup():\n    raise ValueError('炸')\n")
    result = project.cli("check")
    assert result.returncode == 1
    assert "FAILED boom" in result.stderr


def test_json_mode_emits_only_protocol_on_stdout(project: Project) -> None:
    """stdout 是协议通道 —— 日志必须走 stderr。"""
    project.plugin("hello", TOOL)
    env = project.env(MOCK_LLM_SCRIPT=load_script([{"text": "ok"}]))
    result = project.cli("--mode", "json", "任务", env=env)
    assert result.returncode == 0
    for line in result.stdout.strip().splitlines():
        json.loads(line)                        # 每一行都必须是合法 JSON
    assert "sidecar" in result.stderr or result.stderr == ""


def test_json_mode_event_types(project: Project) -> None:
    env = project.env(MOCK_LLM_SCRIPT=load_script([{"text": "回答"}]))
    result = project.cli("--mode", "json", "任务", env=env)
    types = [json.loads(line)["type"] for line in result.stdout.strip().splitlines()]
    assert "reply_start" in types
    assert "reply_end" in types


def test_tools_filter_from_cli(project: Project) -> None:
    project.plugin("a", TOOL.replace("hello", "aaa"))
    project.plugin("b", TOOL.replace("hello", "bbb"))
    result = project.cli("inspect", "--json", "--tools", "aaa")
    report = json.loads(result.stdout)
    assert report["tools"] == ["aaa"]


def test_plugins_flag_adds_directory(project: Project) -> None:
    extra = project.plugin_dir("extra")
    project.write_in(extra, "hello.py", TOOL)
    result = project.cli("inspect", "--json", "--plugins", str(extra))
    report = json.loads(result.stdout)
    assert "hello" in report["tools"]


def test_extension_flag(project: Project) -> None:
    extra = project.plugin_dir("extra")
    project.write_in(extra, "hello.py", TOOL)
    result = project.cli("inspect", "--json", "-e", str(extra / "hello.py"))
    report = json.loads(result.stdout)
    assert "hello" in report["tools"]


def test_model_flag_unknown_provider(project: Project) -> None:
    result = project.cli("inspect", "--model", "nope/x")
    assert result.returncode == 1
    assert "未知的模型 provider" in result.stderr


def test_bad_model_reference(project: Project) -> None:
    result = project.cli("inspect", "--model", "dashscope")
    assert result.returncode == 1
    assert "格式非法" in result.stderr


def test_permission_flag_in_report(project: Project) -> None:
    result = project.cli("inspect", "--json", "--permission", "bypass")
    assert json.loads(result.stdout)["permission"] == "bypass"


def test_system_flag(project: Project) -> None:
    project.plugin("probe", '''
import asyncio
from agentscope_sidecar import Context


async def setup(agent, ctx: Context):
    ctx.provide("prompt_text", asyncio.run(agent._get_system_prompt()))
''')
    result = project.cli("inspect", "--json", "--system", "内联提示词")
    assert result.returncode == 0


def test_unknown_mode_rejected_by_argparse(project: Project) -> None:
    result = project.cli("--mode", "nope")
    assert result.returncode == 2                   # argparse 的用法错误码


def test_quiet_suppresses_info_logs(project: Project) -> None:
    result = project.cli("inspect", "--quiet")
    assert "INFO" not in result.stderr


def test_verbose_enables_debug(project: Project) -> None:
    result = project.cli("inspect", "--verbose")
    assert result.returncode == 0


# ------------------------------------------------------------------ inspect 文本报告的分支


def test_inspect_reports_unresolved_dependency(project: Project) -> None:
    project.plugin("needs", '''
        async def setup(never_provided) -> None:
            pass
    ''')
    result = project.cli("inspect")
    assert result.returncode == 0
    assert "未解析=" in result.stdout
    assert "never_provided" in result.stdout
    assert "未解析的依赖:" in result.stdout


def test_inspect_reports_shadowed_plugin(project: Project) -> None:
    other = project.plugin_dir("other")
    project.write_in(other, "same.py", TOOL.replace("hello", "same"))
    project.plugin("same", TOOL.replace("hello", "same"))

    result = project.cli("inspect", "--plugins", str(other))
    assert result.returncode == 0
    assert "shadowed" in result.stdout
    assert "覆盖" in result.stdout


def test_inspect_reports_failed_plugin(project: Project) -> None:
    project.plugin("boom", "async def setup():\n    raise ValueError('故意炸')\n")
    result = project.cli("inspect")
    assert "failed" in result.stdout
    assert "故意炸" in result.stdout


def test_inspect_reports_warnings(project: Project) -> None:
    """插件目录里有 .py 却没有 ``__init__.py`` 的目录 —— 报出来,别静默跳过。"""
    project.write("plugins/grouped/inner.py", "x = 1\n")
    result = project.cli("inspect")
    assert "告警:" in result.stdout
    assert "没有 __init__.py" in result.stdout


def test_inspect_reports_agent_overrides(project: Project) -> None:
    project.plugin("tuner", '''
        from agentscope.agent import Agent, ReActConfig


        async def setup(agent: Agent) -> None:
            agent.react_config = ReActConfig(max_iters=20)
    ''')
    result = project.cli("inspect")
    assert "Agent 构造参数覆盖:" in result.stdout
    assert "react_config" in result.stdout


def test_inspect_reports_routes(project: Project) -> None:
    project.plugin("web", '''
        from starlette.responses import JSONResponse


        async def index(request):
            return JSONResponse({})


        async def setup(routes) -> None:
            routes.add("GET", "/page", index)
    ''')
    result = project.cli("inspect")
    assert "HTTP 路由:" in result.stdout
    assert "GET /page <- web" in result.stdout


def test_inspect_reports_empty_tools(project: Project) -> None:
    result = project.cli("inspect")
    assert "工具:(无)" in result.stdout
    assert "中间件:(无)" in result.stdout


def test_inspect_reports_services_and_counters(project: Project) -> None:
    result = project.cli("inspect")
    assert "服务提供者:" in result.stdout
    assert "agent" in result.stdout
    assert "事件计数:" in result.stdout
    assert "sidecar/ready" in result.stdout


def test_inspect_marks_builtin_plugins(project: Project) -> None:
    result = project.cli("inspect")
    assert "<builtin>" in result.stdout


def test_list_prints_state_and_id(project: Project) -> None:
    project.plugin("hello", TOOL)
    project.plugin("boom", "async def setup():\n    raise ValueError('x')\n")
    lines = project.cli("list").stdout.strip().splitlines()
    parsed = {line.split()[1]: line.split()[0] for line in lines}
    assert parsed["hello"] == "loaded"
    assert parsed["boom"] == "failed"


def test_check_reports_failed_error_on_stderr(project: Project) -> None:
    project.plugin("boom", "async def setup():\n    raise ValueError('细节')\n")
    result = project.cli("check")
    assert result.returncode == 1
    assert "1 个插件失败" in result.stdout
    assert "细节" in result.stderr


def test_inspect_reports_commands(project: Project) -> None:
    project.plugin("runner", '''
        def go(args: str) -> None:
            pass


        async def setup(commands) -> None:
            commands.add("go", go)
    ''')
    result = project.cli("inspect")
    assert "CLI 命令:" in result.stdout
    assert "go <- runner" in result.stdout


def test_inspect_json_reports_commands(project: Project) -> None:
    project.plugin("runner", '''
        def go(args: str) -> None:
            pass


        async def setup(commands) -> None:
            commands.add("go", go)
    ''')
    report = json.loads(project.cli("inspect", "--json").stdout)
    assert report["commands"] == {"go": "runner"}
