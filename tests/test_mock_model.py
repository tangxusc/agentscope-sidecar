"""离线脚本模型(设计文档 §9.3)。

它是**测试基础设施** —— 全部 e2e 都靠它跑,所以它自己的边界也要测。
"""

from __future__ import annotations

import asyncio
import json

import pytest

from agentscope_sidecar.mock_model import (
    DEFAULT_FALLBACK,
    DEFAULT_SCRIPT,
    ScriptedChatModel,
    load_script,
)


async def _drain(model, messages=None) -> list:
    chunks = []
    async for chunk in await model(messages or []):
        chunks.append(chunk)
    return chunks


def _text_of(chunks: list) -> str:
    for chunk in reversed(chunks):
        if chunk.is_last:
            return "".join(
                getattr(block, "text", "") for block in chunk.content
                if getattr(block, "type", "") == "text"
            )
    return ""


# ------------------------------------------------------------------ 分块语义


def test_text_is_split_into_deltas_then_a_full_final_block() -> None:
    """分片全发非终块,终块携带**完整文本** —— 只发终块会丢 delta,终块只带
    最后一片会让最终消息被截断(§11.7)。"""
    model = ScriptedChatModel("m", [{"text": "一二三四五六七八"}])
    chunks = asyncio.run(_drain(model))

    assert chunks[-1].is_last is True
    assert chunks[-1].content[0].text == "一二三四五六七八"
    assert any(not c.is_last for c in chunks)

    deltas = "".join(
        block.text for c in chunks if not c.is_last for block in c.content
        if block.type == "text"
    )
    assert deltas == "一二三四五六七八"


def test_empty_text_still_produces_a_final_block() -> None:
    model = ScriptedChatModel("m", [{"text": ""}])
    chunks = asyncio.run(_drain(model))
    assert chunks[-1].is_last is True


def test_thinking_step_emits_a_thinking_block() -> None:
    model = ScriptedChatModel("m", [{"thinking": "先想一想", "text": "答案"}])
    chunks = asyncio.run(_drain(model))
    types = [block.type for c in chunks for block in c.content]
    assert "thinking" in types
    assert _text_of(chunks) == "答案"


def test_tool_call_step_emits_start_then_final() -> None:
    model = ScriptedChatModel("m", [
        {"tool_call": {"name": "ping", "args": {"x": 1}}},
    ])
    chunks = asyncio.run(_drain(model))
    assert len(chunks) == 2
    assert chunks[0].is_last is False
    assert chunks[1].is_last is True
    assert chunks[1].content[0].name == "ping"
    assert json.loads(chunks[1].content[0].input) == {"x": 1}


def test_tool_call_ids_are_unique_per_call() -> None:
    model = ScriptedChatModel("m", [
        {"tool_call": {"name": "a", "args": {}}},
        {"tool_call": {"name": "b", "args": {}}},
    ])
    first = asyncio.run(_drain(model))[1].content[0].id
    second = asyncio.run(_drain(model))[1].content[0].id
    assert first != second


def test_script_advances_one_step_per_call() -> None:
    model = ScriptedChatModel("m", [{"text": "第一"}, {"text": "第二"}])
    assert _text_of(asyncio.run(_drain(model))) == "第一"
    assert _text_of(asyncio.run(_drain(model))) == "第二"


# ------------------------------------------------------------------ 脚本耗尽


def test_exhausted_script_repeats_the_last_text() -> None:
    model = ScriptedChatModel("m", [{"text": "唯一"}])
    asyncio.run(_drain(model))
    assert _text_of(asyncio.run(_drain(model))) == "唯一"


def test_exhausted_script_without_text_uses_fallback() -> None:
    model = ScriptedChatModel("m", [{"thinking": "只有思考"}])
    asyncio.run(_drain(model))
    assert _text_of(asyncio.run(_drain(model))) == DEFAULT_FALLBACK


def test_empty_script_uses_fallback() -> None:
    model = ScriptedChatModel("m", [])
    assert _text_of(asyncio.run(_drain(model))) == DEFAULT_FALLBACK


def test_custom_fallback_text() -> None:
    model = ScriptedChatModel("m", [], fallback_text="自定义兜底")
    assert _text_of(asyncio.run(_drain(model))) == "自定义兜底"


def test_model_exposes_a_formatter() -> None:
    """Agent 靠 ``formatter`` 判断能收哪些多模态块 —— 不设会静默出问题。"""
    model = ScriptedChatModel("m", [{"text": "x"}])
    assert type(model.formatter).__name__ == "OpenAIChatFormatter"


# ------------------------------------------------------------------ load_script


def test_load_script_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOCK_LLM_SCRIPT", '[{"text": "env"}]')
    script, fallback = load_script()
    assert script == [{"text": "env"}]
    assert fallback == DEFAULT_FALLBACK


def test_load_script_bad_json_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOCK_LLM_SCRIPT", "{不是 JSON")
    with pytest.raises(ValueError, match="不是合法 JSON"):
        load_script()


def test_load_script_non_list_falls_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """环境变量给了个对象而不是数组 —— 退回默认脚本,不当成脚本用。"""
    monkeypatch.setenv("MOCK_LLM_SCRIPT", '{"text": "对象不是数组"}')
    script, _ = load_script()
    assert script == list(DEFAULT_SCRIPT)


def test_load_script_from_config_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    monkeypatch.delenv("MOCK_LLM_SCRIPT", raising=False)
    monkeypatch.setattr("agentscope_sidecar.mock_model.MODULE_DIR", tmp_path)
    (tmp_path / "config.json").write_text(
        json.dumps({"script": [{"text": "来自文件"}], "fallback_text": "兜底"}),
        encoding="utf-8",
    )
    script, fallback = load_script()
    assert script == [{"text": "来自文件"}]
    assert fallback == "兜底"


def test_load_script_config_without_fields_uses_defaults(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    monkeypatch.delenv("MOCK_LLM_SCRIPT", raising=False)
    monkeypatch.setattr("agentscope_sidecar.mock_model.MODULE_DIR", tmp_path)
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    script, fallback = load_script()
    assert script == list(DEFAULT_SCRIPT)
    assert fallback == DEFAULT_FALLBACK


def test_load_script_defaults_without_any_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    monkeypatch.delenv("MOCK_LLM_SCRIPT", raising=False)
    monkeypatch.setattr("agentscope_sidecar.mock_model.MODULE_DIR", tmp_path)
    assert load_script() == (list(DEFAULT_SCRIPT), DEFAULT_FALLBACK)


def test_env_wins_over_config_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    monkeypatch.setattr("agentscope_sidecar.mock_model.MODULE_DIR", tmp_path)
    (tmp_path / "config.json").write_text(
        json.dumps({"script": [{"text": "文件"}]}), encoding="utf-8",
    )
    monkeypatch.setenv("MOCK_LLM_SCRIPT", '[{"text": "环境"}]')
    assert load_script()[0] == [{"text": "环境"}]
