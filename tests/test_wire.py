"""线格式(设计文档 §11.7)。

``json`` 与 ``rpc`` 共用这一层。它要处理三种输入:AgentScope 的 pydantic 事件、
普通对象、以及构造时直接给的字符串类型名。
"""

from __future__ import annotations

from types import SimpleNamespace

from agentscope_sidecar.modes._wire import (
    event_to_wire,
    event_type,
    message_to_wire,
    now_iso,
)


# ------------------------------------------------------------------ now_iso


def test_now_iso_shape() -> None:
    text = now_iso()
    assert text.endswith("Z")
    assert "T" in text
    assert "+00:00" not in text


# ------------------------------------------------------------------ event_type


def test_event_type_lowercases_enum_name() -> None:
    """AgentScope 的成员名是大写,线格式要小写。"""
    assert event_type(SimpleNamespace(type="REPLY_START")) == "reply_start"


def test_event_type_reads_enum_value() -> None:
    class _Type:
        value = "TOOL_CALL_START"

    assert event_type(SimpleNamespace(type=_Type())) == "tool_call_start"


def test_event_type_strips_enum_repr_prefix() -> None:
    """构造时直接给 ``"EventType.X"`` 字符串的也要归一。"""
    assert event_type(SimpleNamespace(type="EventType.MODEL_CALL_END")) == (
        "model_call_end"
    )


def test_event_type_without_type_attribute() -> None:
    assert event_type(object()) == "unknown"


# ------------------------------------------------------------------ event_to_wire


def test_event_to_wire_uses_model_dump() -> None:
    class _Event:
        type = "REPLY_START"

        def model_dump(self, mode: str = "python") -> dict:
            assert mode == "json"
            return {"type": "REPLY_START", "id": "internal", "name": "agent"}

    payload = event_to_wire(_Event(), "rid")
    assert payload["type"] == "reply_start"
    assert payload["name"] == "agent"
    assert payload["reply_id"] == "rid"
    assert "id" not in payload                      # 噪声字段被丢掉
    assert payload["ts"].endswith("Z")


def test_event_to_wire_normalizes_dumped_type() -> None:
    """``model_dump`` 出来的 ``type`` 被归一后的值覆盖 —— 只有一处实现。"""
    class _Event:
        type = "EventType.TOOL_CALL_END"

        def model_dump(self, mode: str = "python") -> dict:
            return {"type": "TOOL_CALL_END"}

    assert event_to_wire(_Event())["type"] == "tool_call_end"


def test_event_to_wire_falls_back_to_dict() -> None:
    """没有 ``model_dump`` 的普通对象走 ``vars``。"""
    event = SimpleNamespace(type="CUSTOM", created_at="2024", metadata={"a": 1},
                            value=7)
    payload = event_to_wire(event)
    assert payload["value"] == 7
    assert "created_at" not in payload
    assert "metadata" not in payload
    assert "reply_id" not in payload                # 没传就不加


def test_event_to_wire_handles_object_without_dict() -> None:
    class _Slotted:
        __slots__ = ()

    payload = event_to_wire(_Slotted())
    assert payload["type"] == "unknown"
    assert "ts" in payload


def test_event_to_wire_dump_failure_falls_back() -> None:
    class _Bad:
        def __init__(self) -> None:
            self.type = "X"

        def model_dump(self, mode: str = "python") -> dict:
            raise RuntimeError("dump 不支持这个模式")

    payload = event_to_wire(_Bad())
    assert payload["type"] == "x"


# ------------------------------------------------------------------ message_to_wire


def test_message_to_wire_dumps_blocks() -> None:
    class _Block:
        def model_dump(self, mode: str = "python") -> dict:
            return {"type": "text", "text": "你好"}

    payload = message_to_wire(SimpleNamespace(content=[_Block()]))
    assert payload == {"content": [{"type": "text", "text": "你好"}]}


def test_message_to_wire_unknown_block_keeps_repr() -> None:
    """认不出的块不丢 —— 留 ``repr`` 供排查,总比静默少一段内容好。"""
    payload = message_to_wire(SimpleNamespace(content=["裸字符串"]))
    assert payload["content"] == [{"type": "unknown", "repr": "'裸字符串'"}]


def test_message_to_wire_empty_and_missing_content() -> None:
    assert message_to_wire(SimpleNamespace(content=[])) == {"content": []}
    assert message_to_wire(SimpleNamespace(content=None)) == {"content": []}
    assert message_to_wire(SimpleNamespace()) == {"content": []}


def test_message_to_wire_block_dump_failure() -> None:
    class _Bad:
        def model_dump(self, mode: str = "python") -> dict:
            raise RuntimeError("不行")

    payload = message_to_wire(SimpleNamespace(content=[_Bad()]))
    assert payload["content"][0]["type"] == "unknown"
