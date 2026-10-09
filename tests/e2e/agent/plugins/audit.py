"""中间件 + 提示词片段。"""

from __future__ import annotations

from agentscope.agent import Agent
from agentscope.middleware import MiddlewareBase

CALLS: list[str] = []


class AuditTrail(MiddlewareBase):
    """记录每次模型调用的模型名。"""

    async def on_model_call(self, agent: Agent, input_kwargs, next_handler):
        CALLS.append("model_call")
        return await next_handler(**input_kwargs)


class PromptSection(MiddlewareBase):
    """往系统提示词追加一段。"""

    async def on_system_prompt(self, agent: Agent, current_prompt: str) -> str:
        return f"{current_prompt}\n\n# 审计\n\n所有工具调用都会被记录。"


async def setup(agent: Agent) -> None:
    agent._model_call_middlewares.append(AuditTrail())
    agent._system_prompt_middlewares.append(PromptSection())
