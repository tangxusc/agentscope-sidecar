"""中间件 + 提示词片段。

两个 hook 的签名不一样:

- ``on_model_call`` 是**链式**的 —— 拿到 ``next_handler``,必须调它。
- ``on_system_prompt`` 是**纯变换** —— 收到当前提示词,返回新的。没有 ``next_handler``。

两者都必须是 ``async def``,同步实现会被静默跳过。
"""

from __future__ import annotations

from pathlib import Path

from agentscope.agent import Agent
from agentscope.middleware import MiddlewareBase

from agentscope_sidecar import Effect

LOG = Path(__file__).parent.parent / "model_calls.log"


class ModelCallAudit(MiddlewareBase):
    """记录每一次模型调用。"""

    async def on_model_call(self, agent: Agent, input_kwargs, next_handler):
        with LOG.open("a", encoding="utf-8") as handle:
            handle.write("model_call\n")
        return await next_handler(**input_kwargs)


class HouseRules(MiddlewareBase):
    """往系统提示词追加一段。"""

    async def on_system_prompt(self, agent: Agent, current_prompt: str) -> str:
        return f"{current_prompt}\n\n# 团队约定\n\n回答要短,先给结论。"


async def setup(agent: Agent) -> Effect:
    # 直接往桶里 append。不要改 agent.middlewares —— 那是构造参数,构造后不再被读
    audit = ModelCallAudit()
    rules = HouseRules()
    agent._model_call_middlewares.append(audit)
    agent._system_prompt_middlewares.append(rules)

    def cleanup() -> None:
        agent._model_call_middlewares.remove(audit)
        agent._system_prompt_middlewares.remove(rules)
        LOG.unlink(missing_ok=True)

    return cleanup
