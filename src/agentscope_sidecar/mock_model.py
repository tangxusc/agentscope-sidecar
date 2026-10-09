"""内置离线脚本模型(设计文档 §9.3)。

**不是插件** —— 它是 ``models.build_model()`` 用的一个模型构造器。底座在
没有任何模型凭据时兜底到它,因此全部测试不需要 API 凭据。

脚本来源(按优先级):

1. 环境变量 ``MOCK_LLM_SCRIPT``(JSON 数组)
2. 本模块目录下的 ``config.json`` 的 ``script`` 字段

脚本步格式::

    {"tool_call": {"name": "get_weather", "args": {"city": "北京"}}}
    {"text": "北京当前晴，25℃。"}
    {"thinking": "先查天气"}

每步消耗一次模型调用。脚本耗尽后重复最后一个 ``text`` 步。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, AsyncGenerator

from agentscope.credential import CredentialBase
from agentscope.formatter import OpenAIChatFormatter
from agentscope.message import TextBlock, ThinkingBlock, ToolCallBlock
from agentscope.model import (
    ChatModelBase,
    ChatResponse,
    ChatUsage,
    FinishedReason,
)

MODULE_DIR = Path(__file__).parent
DEFAULT_SCRIPT: list[dict[str, Any]] = [
    {"text": "(mock 模型默认回答)"},
]
DEFAULT_FALLBACK = "(脚本已耗尽,无更多输出)"

#: 文本按此长度切块,产生多个 is_last=False 的响应 —— 只有这样 Agent 才会发出
#: TEXT_BLOCK_DELTA 事件(§11.7)。
CHUNK_SIZE = 6


class _NoCredential(CredentialBase):
    """占位凭据。离线模型不访问网络。"""


class ScriptedChatModel(ChatModelBase):
    """按脚本顺序回放响应。"""

    def __init__(
        self,
        model: str,
        script: list[dict[str, Any]],
        fallback_text: str = DEFAULT_FALLBACK,
    ) -> None:
        super().__init__(
            credential=_NoCredential(), model=model, parameters=None, stream=True,
        )
        # Agent 会读 model.formatter 判断可接收的多模态类型 —— 必须设置
        self.formatter = OpenAIChatFormatter()
        self._script = list(script or [])
        self._fallback_text = fallback_text
        self._cursor = 0
        self._call_seq = 0

    # ------------------------------------------------------------------ 脚本

    def _next_step(self) -> dict[str, Any]:
        if self._cursor < len(self._script):
            step = self._script[self._cursor]
            self._cursor += 1
            return step
        for step in reversed(self._script):
            if "text" in step:
                return {"text": step["text"]}
        return {"text": self._fallback_text}

    # ------------------------------------------------------------- 生成响应

    async def __call__(
        self,
        messages: list[Any],
        tools: list[dict] | None = None,
        tool_choice: Any = None,
        **kwargs: Any,
    ) -> AsyncGenerator[ChatResponse, None]:
        """返回异步生成器(AgentScope 约定:await 后得到生成器)。"""
        self._call_seq += 1
        seq = self._call_seq
        step = self._next_step()

        async def _generate() -> AsyncGenerator[ChatResponse, None]:
            usage = ChatUsage(
                input_tokens=len(str(messages)), output_tokens=len(str(step)),
                time=0.0,
            )
            if "tool_call" in step:
                for chunk in self._tool_call_chunks(step["tool_call"], seq, usage):
                    yield chunk
                return
            for chunk in self._text_chunks(step, seq, usage):
                yield chunk

        return _generate()

    def _tool_call_chunks(
        self, spec: dict[str, Any], seq: int, usage: ChatUsage,
    ) -> list[ChatResponse]:
        """先一个非终块(触发 TOOL_CALL_START/DELTA),再一个终块(提交调用)。

        终块的 content 就是本轮响应,Agent 依据它决定是否执行工具。
        """
        block_id = f"call_{seq}"
        payload = json.dumps(spec.get("args", {}), ensure_ascii=False)

        def make() -> ToolCallBlock:
            return ToolCallBlock(
                type="tool_call", id=block_id, name=spec["name"], input=payload,
            )

        return [
            ChatResponse(content=[make()], is_last=False, usage=usage),
            ChatResponse(content=[make()], is_last=True,
                         finished_reason=FinishedReason.COMPLETED, usage=usage),
        ]

    def _text_chunks(
        self, step: dict[str, Any], seq: int, usage: ChatUsage,
    ) -> list[ChatResponse]:
        """所有文本以**非终块**逐块给出(产生 TEXT_BLOCK_DELTA),最后一个
        **终块携带完整文本** —— 终块的 content 决定最终 Msg(§11.7)。

        只发一个终块会让 delta 全部丢失;终块只带最后一片会让最终消息被截断。
        """
        chunks: list[ChatResponse] = []

        if "thinking" in step:
            chunks.append(ChatResponse(
                content=[ThinkingBlock(type="thinking", thinking=step["thinking"])],
                is_last=False, usage=usage,
            ))

        text = step.get("text", self._fallback_text)
        block_id = f"text_{seq}"
        pieces = [text[i:i + CHUNK_SIZE] for i in range(0, len(text), CHUNK_SIZE)]
        pieces = pieces or [""]

        for piece in pieces:
            chunks.append(ChatResponse(
                content=[TextBlock(type="text", text=piece, id=block_id)],
                is_last=False, usage=usage,
            ))

        chunks.append(ChatResponse(
            content=[TextBlock(type="text", text=text, id=block_id)],
            is_last=True, finished_reason=FinishedReason.COMPLETED, usage=usage,
        ))
        return chunks


def load_script() -> tuple[list[dict[str, Any]], str]:
    """读脚本。环境变量优先于 ``config.json``。"""
    raw = os.environ.get("MOCK_LLM_SCRIPT")
    if raw:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"MOCK_LLM_SCRIPT 不是合法 JSON:{exc}") from exc
        if isinstance(data, list):
            return data, DEFAULT_FALLBACK

    config = MODULE_DIR / "config.json"
    if config.is_file():
        data = json.loads(config.read_text(encoding="utf-8"))
        return (
            list(data.get("script") or DEFAULT_SCRIPT),
            data.get("fallback_text", DEFAULT_FALLBACK),
        )

    return list(DEFAULT_SCRIPT), DEFAULT_FALLBACK


__all__ = ["CHUNK_SIZE", "ScriptedChatModel", "load_script"]
