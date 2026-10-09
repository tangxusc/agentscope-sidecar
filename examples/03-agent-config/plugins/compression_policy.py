"""改 Agent 的构造参数。

AgentScope 的 ``Agent`` 构造后完全可变 —— 所有字段都是普通属性,调用时才读。
所以插件直接赋值即可,不需要底座提供配置通道。

参数名与 ``Agent.__init__`` 一致,查 AgentScope 那一份文档就够。

``setup`` 里把原对象留住,effect 里再放回去 —— 卸载插件后配置回到原样。
"""

from __future__ import annotations

from agentscope.agent import Agent, ContextConfig, InjectionConfig, ReActConfig

from agentscope_sidecar import Effect


async def setup(agent: Agent) -> Effect:
    previous = (agent.context_config, agent.injection_config, agent.react_config)

    agent.context_config = ContextConfig(
        trigger_ratio=0.7,          # 更早触发上下文压缩(默认 0.8,上限 0.9)
        tool_result_limit=20000,    # 工具结果超过就截断(默认 50000)
    )
    agent.injection_config = InjectionConfig(timezone="Asia/Shanghai")
    agent.react_config = ReActConfig(max_iters=20, stop_on_reject=True)

    def cleanup() -> None:
        (agent.context_config,
         agent.injection_config,
         agent.react_config) = previous

    return cleanup
