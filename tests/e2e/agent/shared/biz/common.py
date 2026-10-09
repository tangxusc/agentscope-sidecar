"""与 base/common 同名 —— 生效的是这一份。"""

from __future__ import annotations

from agentscope.agent import Agent
from agentscope.middleware import MiddlewareBase


class BizMarker(MiddlewareBase):
    async def on_model_call(self, agent: Agent, input_kwargs, next_handler):
        return await next_handler(**input_kwargs)


async def setup(agent: Agent) -> None:
    agent._model_call_middlewares.append(BizMarker())
