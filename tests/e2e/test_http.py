"""HTTP 模式:内置路由 + 插件路由(设计文档 §11.6)。

``dashboard`` 插件注册了 ``GET /`` ``GET /stats`` ``POST /stats`` —— 用真实的
夹具路由,不用测试里现拼的插件。
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest

from helpers import load_script


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _request(
    url: str, *, method: str = "GET", body: bytes | None = None,
) -> tuple[int, str]:
    request = urllib.request.Request(url, data=body, method=method)
    if body is not None:
        request.add_header("content-type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


@pytest.fixture
def http_server(e2e):
    port = free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "agentscope_sidecar",
         "--mode", "http", "--port", str(port)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env=e2e.env(), cwd=e2e.root,
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 40
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"{base}/health", timeout=2).read()
            break
        except Exception:
            if proc.poll() is not None:
                raise RuntimeError(f"服务提前退出:{proc.stderr.read()[-800:]}")
            time.sleep(0.3)
    else:
        proc.terminate()
        raise RuntimeError("服务未在超时内就绪")

    try:
        yield base
    finally:
        proc.terminate()
        proc.wait(timeout=15)


# ------------------------------------------------------------------ 内置路由


def test_health(http_server: str) -> None:
    status, body = _request(f"{http_server}/health")
    assert status == 200
    payload = json.loads(body)
    assert payload["status"] == "ok"
    assert payload["plugins"]["loaded"] >= 10


def test_info_lists_plugin_tools(http_server: str) -> None:
    status, body = _request(f"{http_server}/info")
    assert status == 200
    payload = json.loads(body)
    assert "get_weather" in payload["tools"]
    assert payload["tool_owners"]["get_weather"] == "weather"


def test_info_lists_plugin_routes(http_server: str) -> None:
    _, body = _request(f"{http_server}/info")
    routes = json.loads(body)["routes"]
    assert routes["GET /stats"] == "dashboard"
    assert routes["POST /stats"] == "dashboard"


def test_info_lists_agent_overrides(http_server: str) -> None:
    _, body = _request(f"{http_server}/info")
    overrides = json.loads(body)["agent_overrides"]
    assert overrides["react_config"] == "agent_tuning"


def test_unknown_path_is_404(http_server: str) -> None:
    status, _ = _request(f"{http_server}/nope")
    assert status == 404


# ------------------------------------------------------------------ 插件路由


def test_plugin_get_route(http_server: str) -> None:
    status, body = _request(f"{http_server}/stats")
    assert status == 200
    assert json.loads(body) == {"page": "stats"}


def test_plugin_root_route(http_server: str) -> None:
    """路径是**完整路径**,底座不加前缀。"""
    status, body = _request(f"{http_server}/")
    assert status == 200
    assert json.loads(body) == {"page": "index"}


def test_plugin_post_route(http_server: str) -> None:
    status, body = _request(
        f"{http_server}/stats", method="POST", body=b"{}",
    )
    assert status == 201
    assert json.loads(body) == {"created": True}


def test_wrong_method_is_405(http_server: str) -> None:
    status, _ = _request(f"{http_server}/stats", method="DELETE")
    assert status == 405


# ------------------------------------------------------------------ prompt / SSE


def test_prompt_returns_reply_id(e2e) -> None:
    port = free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "agentscope_sidecar",
         "--mode", "http", "--port", str(port)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env=e2e.env(MOCK_LLM_SCRIPT=load_script([{"text": "回答"}])),
        cwd=e2e.root,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.time() + 40
        while time.time() < deadline:
            try:
                urllib.request.urlopen(f"{base}/health", timeout=2).read()
                break
            except Exception:
                time.sleep(0.3)

        status, body = _request(
            f"{base}/prompt", method="POST",
            body=json.dumps({"text": "你好"}).encode(),
        )
        assert status == 200
        payload = json.loads(body)
        assert "reply_id" in payload

        # 跟着 reply_id 读一次事件流 —— 服务端应把整轮跑完
        status, stream = _request(f"{base}/events/{payload['reply_id']}")
        assert status == 200
        assert "data:" in stream
    finally:
        proc.terminate()
        proc.wait(timeout=15)


def test_prompt_rejects_empty_text(http_server: str) -> None:
    status, body = _request(
        f"{http_server}/prompt", method="POST",
        body=json.dumps({"text": ""}).encode(),
    )
    assert status == 400
    assert "text" in json.loads(body)["error"]


def test_prompt_rejects_non_json(http_server: str) -> None:
    status, body = _request(
        f"{http_server}/prompt", method="POST", body=b"not json",
    )
    assert status == 400
    assert "JSON" in json.loads(body)["error"]


def test_events_unknown_reply_streams_and_ends(http_server: str) -> None:
    """未知 ``reply_id`` 不是 404 —— 返回一条带 error 帧后立刻结束的事件流。

    客户端拿到 ``reply_id`` 与订阅之间可能有时差,给 404 会让正常时序也报错。
    """
    status, body = _request(f"{http_server}/events/nope")
    assert status == 200
    assert "event: error" in body
    assert "unknown reply_id" in body


def test_cancel_unknown_reply(http_server: str) -> None:
    status, body = _request(
        f"{http_server}/cancel", method="POST",
        body=json.dumps({"reply_id": "nope"}).encode(),
    )
    assert status == 200
    assert json.loads(body)["cancelled"] is False


# ------------------------------------------------------------------ 端口


def test_port_in_use_exits_3(e2e) -> None:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
        result = subprocess.run(
            [sys.executable, "-m", "agentscope_sidecar",
             "--mode", "http", "--port", str(port)],
            capture_output=True, text=True, env=e2e.env(), cwd=e2e.root,
            timeout=60,
        )
    assert result.returncode == 3


def test_default_host_is_loopback(e2e) -> None:
    """默认绑 ``127.0.0.1`` —— 不绑 ``0.0.0.0``。"""
    from pathlib import Path

    from agentscope_sidecar import CliOptions
    from agentscope_sidecar.convention import resolve

    assert resolve(CliOptions(), cwd=Path(e2e.root)).host == "127.0.0.1"


# ------------------------------------------------------------------ 并发与取消


@pytest.fixture
def slow_server(e2e):
    """脚本让模型先调一个睡 3 秒的工具 —— 用来制造「进行中」的 prompt。"""
    port = free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "agentscope_sidecar",
         "--mode", "http", "--port", str(port)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env=e2e.env(MOCK_LLM_SCRIPT=load_script([
            {"tool_call": {"name": "wait", "args": {"seconds": 3.0}}},
            {"text": "结束"},
        ])),
        cwd=e2e.root,
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 40
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"{base}/health", timeout=2).read()
            break
        except Exception:
            if proc.poll() is not None:
                raise RuntimeError(f"服务提前退出:{proc.stderr.read()[-800:]}")
            time.sleep(0.3)
    else:
        proc.terminate()
        raise RuntimeError("服务未在超时内就绪")

    try:
        yield base
    finally:
        proc.terminate()
        proc.wait(timeout=15)


def test_second_prompt_is_rejected_while_busy(slow_server: str) -> None:
    status, body = _request(
        f"{slow_server}/prompt", method="POST",
        body=json.dumps({"text": "第一个"}).encode(),
    )
    assert status == 200
    assert "reply_id" in json.loads(body)

    status, body = _request(
        f"{slow_server}/prompt", method="POST",
        body=json.dumps({"text": "第二个"}).encode(),
    )
    assert status == 409
    assert "进行中" in json.loads(body)["error"]


def test_cancel_active_prompt(slow_server: str) -> None:
    _, body = _request(
        f"{slow_server}/prompt", method="POST",
        body=json.dumps({"text": "跑起来"}).encode(),
    )
    reply_id = json.loads(body)["reply_id"]

    status, body = _request(
        f"{slow_server}/cancel", method="POST",
        body=json.dumps({"reply_id": reply_id}).encode(),
    )
    assert status == 200
    assert json.loads(body)["cancelled"] is True


def test_cancel_with_non_json_body(slow_server: str) -> None:
    status, body = _request(
        f"{slow_server}/cancel", method="POST", body=b"not json",
    )
    assert status == 200
    assert json.loads(body)["cancelled"] is False


def test_prompt_with_accept_sse_streams_inline(slow_server: str) -> None:
    """``Accept: text/event-stream`` 时 ``/prompt`` 直接返回事件流。"""
    request = urllib.request.Request(
        f"{slow_server}/prompt",
        data=json.dumps({"text": "流式"}).encode(),
        method="POST",
    )
    request.add_header("content-type", "application/json")
    request.add_header("accept", "text/event-stream")
    with urllib.request.urlopen(request, timeout=30) as response:
        assert response.status == 200
        assert "text/event-stream" in response.headers["content-type"]
        body = response.read().decode()
    assert "data:" in body


def test_plugin_route_error_becomes_500(project) -> None:
    project.plugin("boom_route", '''
        async def explode(request):
            raise RuntimeError("插件路由故意炸")


        async def setup(routes) -> None:
            routes.add("GET", "/boom", explode)
    ''')
    port = free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "agentscope_sidecar",
         "--mode", "http", "--port", str(port)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env=project.env(), cwd=project.root,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.time() + 40
        while time.time() < deadline:
            try:
                urllib.request.urlopen(f"{base}/health", timeout=2).read()
                break
            except Exception:
                time.sleep(0.3)
        status, body = _request(f"{base}/boom")
        assert status == 500
        assert "插件路由故意炸" in json.loads(body)["detail"]
    finally:
        proc.terminate()
        proc.wait(timeout=15)


def test_plugin_route_may_return_plain_dict(project) -> None:
    project.plugin("dict_route", '''
        async def data(request):
            return {"plain": True}


        async def setup(routes) -> None:
            routes.add("GET", "/data", data)
    ''')
    port = free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "agentscope_sidecar",
         "--mode", "http", "--port", str(port)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env=project.env(), cwd=project.root,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.time() + 40
        while time.time() < deadline:
            try:
                urllib.request.urlopen(f"{base}/health", timeout=2).read()
                break
            except Exception:
                time.sleep(0.3)
        status, body = _request(f"{base}/data")
        assert status == 200
        assert json.loads(body) == {"plain": True}
    finally:
        proc.terminate()
        proc.wait(timeout=15)
