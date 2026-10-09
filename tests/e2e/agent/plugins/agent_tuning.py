"""改 Agent 的构造参数。"""

from __future__ import annotations

from agentscope.agent import Agent, ContextConfig, InjectionConfig, ReActConfig

from agentscope_sidecar import Effect


async def setup(agent: Agent) -> Effect:
    agent.context_config = ContextConfig(trigger_ratio=0.7, tool_result_limit=20000)
    agent.injection_config = InjectionConfig(timezone="Asia/Shanghai")
    agent.react_config = ReActConfig(max_iters=20, stop_on_reject=True)

    previous = agent.context_config

    def cleanup() -> None:
        agent.react_config = ReActConfig()
        assert previous.trigger_ratio == 0.7

    return cleanup
