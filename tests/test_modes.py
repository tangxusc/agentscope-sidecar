"""RPC 与 console 模式(子进程)。

HTTP 的端到端在 ``tests/e2e/test_http.py``;这里只补 RPC 的协议面与 ``console``
的交互循环 —— 那两处只能靠真实子进程覆盖。
"""

from __future__ import annotations

import json
import subprocess
import sys

from helpers import Project, load_script


def rpc(
    project: Project, *requests: dict, env: dict | None = None, timeout: int = 90,
) -> subprocess.CompletedProcess[str]:
    payload = "".join(json.dumps(item) + "\n" for item in requests)
    return subprocess.run(
        [sys.executable, "-m", "agentscope_sidecar", "--mode", "rpc"],
        input=payload,
        capture_output=True, text=True, env=env or project.env(),
        cwd=project.root, timeout=timeout,
    )


def lines(stdout: str) -> list[dict]:
    return [json.loads(line) for line in stdout.splitlines() if line.strip()]


def replies_for(stdout: str, req_id: object) -> list[dict]:
    return [item for item in lines(stdout) if item.get("id") == req_id]


def notifications(stdout: str, kind: str | None = None) -> list[dict]:
    out = [item for item in lines(stdout) if "id" not in item]
    if kind is not None:
        out = [item for item in out if item.get("method") == kind]
    return out


# ------------------------------------------------------------------ 协议


def test_ping(project: Project) -> None:
    result = rpc(project, {"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert result.returncode == 0
    reply = replies_for(result.stdout, 1)[0]
    assert reply["result"] == {"pong": True}
    assert reply["jsonrpc"] == "2.0"


def test_initialize_reports_agent_state(project: Project) -> None:
    project.plugin("probe", '''
        from agentscope.agent import Agent
        from agentscope.message import TextBlock
        from agentscope.tool import FunctionTool, ToolResponse


        async def probe() -> ToolResponse:
            """探测。"""
            return ToolResponse(content=[TextBlock(type="text", text="ok")])


        async def setup(agent: Agent) -> None:
            await agent.toolkit.add_tool(FunctionTool(probe, is_read_only=True))
    ''')
    reply = replies_for(
        rpc(project, {"jsonrpc": "2.0", "id": 2, "method": "initialize"}).stdout, 2,
    )[0]["result"]
    assert reply["mode"] == "rpc"
    assert reply["permission"] == "default"
    assert reply["tools"] == ["probe"]
    assert "probe" in reply["plugins"]
    assert isinstance(reply["routes"], dict)


def test_unknown_method(project: Project) -> None:
    reply = replies_for(
        rpc(project, {"jsonrpc": "2.0", "id": 3, "method": "nope"}).stdout, 3,
    )[0]
    assert reply["error"]["code"] == -32601
    assert "nope" in reply["error"]["message"]


def test_bad_json(project: Project) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "agentscope_sidecar", "--mode", "rpc"],
        input="这不是 JSON\n",
        capture_output=True, text=True, env=project.env(), cwd=project.root,
        timeout=60,
    )
    assert lines(result.stdout)[0]["error"]["code"] == -32700


def test_non_object_request(project: Project) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "agentscope_sidecar", "--mode", "rpc"],
        input="[1, 2]\n",
        capture_output=True, text=True, env=project.env(), cwd=project.root,
        timeout=60,
    )
    assert lines(result.stdout)[0]["error"]["code"] == -32600


def test_blank_lines_are_ignored(project: Project) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "agentscope_sidecar", "--mode", "rpc"],
        input="\n\n" + json.dumps({"id": 1, "method": "ping"}) + "\n",
        capture_output=True, text=True, env=project.env(), cwd=project.root,
        timeout=60,
    )
    assert len(lines(result.stdout)) == 1


def test_stdout_is_protocol_only(project: Project) -> None:
    result = rpc(project, {"jsonrpc": "2.0", "id": 1, "method": "ping"})
    for line in result.stdout.splitlines():
        json.loads(line)                       # 非 JSON 会在这里炸


def test_eof_ends_the_loop(project: Project) -> None:
    """stdin 关闭 = shutdown,退出码 0。"""
    result = rpc(project, {"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert result.returncode == 0


# ------------------------------------------------------------------ prompt


def test_prompt_returns_reply_id_and_streams_events(project: Project) -> None:
    env = project.env(MOCK_LLM_SCRIPT=load_script([{"text": "来自 rpc"}]))
    result = rpc(
        project,
        {"jsonrpc": "2.0", "id": 9, "method": "prompt",
         "params": {"text": "你好"}},
        env=env,
    )
    reply = replies_for(result.stdout, 9)[0]
    assert "reply_id" in reply["result"]

    events = notifications(result.stdout, "event")
    assert events
    assert any(item["params"]["type"] == "reply_end" for item in events)


def test_prompt_requires_non_empty_text(project: Project) -> None:
    reply = replies_for(
        rpc(project, {"jsonrpc": "2.0", "id": 5, "method": "prompt",
                      "params": {}}).stdout,
        5,
    )[0]
    assert reply["error"]["code"] == -32602


def test_prompt_rejects_second_concurrent_call(project: Project) -> None:
    """同一 session 不允许并发 prompt。

    第一个 prompt 必须**还在跑**才能测到 —— 用一个会睡一会儿的工具把它拖住,
    否则 mock 模型瞬间返回,``active_session`` 已经清空,第二个就顺理成章地受理了。
    """
    project.plugin("slow", '''
        import asyncio

        from agentscope.agent import Agent
        from agentscope.message import TextBlock
        from agentscope.tool import FunctionTool, ToolResponse


        async def wait(seconds: float = 3.0) -> ToolResponse:
            """等一会儿。

            Args:
                seconds (float): 秒数
            """
            await asyncio.sleep(seconds)
            return ToolResponse(content=[TextBlock(type="text", text="等完了")])


        async def setup(agent: Agent) -> None:
            await agent.toolkit.add_tool(FunctionTool(wait, is_read_only=True))
    ''')
    env = project.env(MOCK_LLM_SCRIPT=load_script([
        {"tool_call": {"name": "wait", "args": {"seconds": 3.0}}},
        {"text": "结束"},
    ]))
    result = rpc(
        project,
        {"jsonrpc": "2.0", "id": 1, "method": "prompt", "params": {"text": "a"}},
        {"jsonrpc": "2.0", "id": 2, "method": "prompt", "params": {"text": "b"}},
        {"jsonrpc": "2.0", "id": 3, "method": "shutdown"},
        env=env,
    )
    codes = [
        replies_for(result.stdout, i)[0].get("error", {}).get("code")
        for i in (1, 2)
    ]
    assert codes[0] is None                    # 第一个受理
    assert codes[1] == -32001                  # 第二个 BUSY


def test_cancel_unknown_reply(project: Project) -> None:
    reply = replies_for(
        rpc(project, {"jsonrpc": "2.0", "id": 7, "method": "cancel",
                      "params": {"reply_id": "nope"}}).stdout,
        7,
    )[0]
    assert reply["result"] == {"cancelled": False}


def test_cancel_without_reply_id(project: Project) -> None:
    reply = replies_for(
        rpc(project, {"jsonrpc": "2.0", "id": 8, "method": "cancel",
                      "params": {}}).stdout,
        8,
    )[0]
    assert reply["result"] == {"cancelled": False}


def test_shutdown_stops_the_loop(project: Project) -> None:
    result = rpc(
        project,
        {"jsonrpc": "2.0", "id": 1, "method": "shutdown"},
        {"jsonrpc": "2.0", "id": 2, "method": "ping"},     # 不该被处理
    )
    assert replies_for(result.stdout, 1)[0]["result"] == {}
    assert replies_for(result.stdout, 2) == []
    assert result.returncode == 0


# ------------------------------------------------------------------ console


def console(project: Project, *inputs: str, env: dict | None = None,
            timeout: int = 90) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "agentscope_sidecar", "--mode", "console"],
        input="".join(line + "\n" for line in inputs),
        capture_output=True, text=True, env=env or project.env(),
        cwd=project.root, timeout=timeout,
    )


def test_console_runs_one_round(project: Project) -> None:
    env = project.env(MOCK_LLM_SCRIPT=load_script([{"text": "控制台回答"}]))
    result = console(project, "你好", "", env=env)
    assert result.returncode == 0
    assert "控制台回答" in result.stdout
    assert "输入任务" in result.stderr          # 提示走 stderr


def test_console_exits_on_eof(project: Project) -> None:
    result = console(project)
    assert result.returncode == 0


def test_console_blank_line_exits(project: Project) -> None:
    env = project.env(MOCK_LLM_SCRIPT=load_script([{"text": "不该出现"}]))
    result = console(project, "", env=env)
    assert result.returncode == 0
    assert "不该出现" not in result.stdout


def test_console_multiple_rounds(project: Project) -> None:
    env = project.env(
        MOCK_LLM_SCRIPT=load_script([{"text": "第一"}, {"text": "第二"}]),
    )
    result = console(project, "一", "二", "", env=env)
    assert "第一" in result.stdout
    assert "第二" in result.stdout


def test_console_is_interactive(project: Project) -> None:
    """``console`` 下 ``ctx.interactive`` 为 True —— 别的模式都做不到。"""
    project.plugin("probe", '''
        from pathlib import Path

        from agentscope_sidecar import Context

        LOG = Path(__file__).parent.parent / "interactive.txt"


        async def setup(ctx: Context) -> None:
            LOG.write_text(str(ctx.interactive), encoding="utf-8")
    ''')
    env = project.env(MOCK_LLM_SCRIPT=load_script([{"text": "ok"}]))
    console(project, "", env=env)
    assert (project.root / "interactive.txt").read_text(encoding="utf-8") == "True"


# ------------------------------------------------------------------ json 错误路径


def test_json_mode_requires_task(project: Project) -> None:
    result = project.cli("--mode", "json")
    assert result.returncode != 0
    assert "需要一个任务参数" in (result.stderr + result.stdout)


def test_json_mode_reports_model_errors(project: Project) -> None:
    """模型抛错要变成一条 ``error`` 帧,而不是崩掉进程。"""
    result = project.cli(
        "--mode", "json", "任务",
        env=project.env(MOCK_LLM_SCRIPT="[{\"text\": \"ok\"}]"),
    )
    assert result.returncode == 0
    assert any(item["type"] == "result" for item in lines(result.stdout))


def test_once_mode_requires_task(project: Project) -> None:
    result = project.cli("--mode", "once")
    assert result.returncode != 0
    assert "需要一个任务参数" in (result.stderr + result.stdout)


def test_once_mode_prints_text_only(project: Project) -> None:
    result = project.cli(
        "打个招呼",
        env=project.env(MOCK_LLM_SCRIPT=load_script([{"text": "只有文本"}])),
    )
    assert result.returncode == 0
    assert "只有文本" in result.stdout
    assert "{" not in result.stdout              # 没有协议帧
