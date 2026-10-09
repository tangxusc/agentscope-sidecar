"""两个目录里都有同名插件 —— 高优先级那个整个取代低优先级。"""

from __future__ import annotations

from agentscope.agent import Agent
from agentscope.middleware import MiddlewareBase


class BaseMarker(MiddlewareBase):
    async def on_model_call(self, agent: Agent, input_kwargs, next_handler):
        return await next_handler(**input_kwargs)


async def setup(agent: Agent) -> None:
    agent._model_call_middlewares.append(BaseMarker())
