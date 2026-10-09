"""内置插件:系统提示词(设计文档 §7.1)。

读运行目录下的 ``SYSTEM.md``(或 ``--system`` 内联文本),作为提示词片段挂上。

**用 ``on_system_prompt`` 中间件而不是直接赋 ``agent._system_prompt``** ——
直接赋值是覆盖,后加载的插件会把它顶掉。中间件是追加。

优先级最低:用户插件可以放一个同名插件覆盖它。
"""

from __future__ import annotations

from agentscope.agent import Agent
from agentscope.middleware import MiddlewareBase

from agentscope_sidecar.effects import Effect


class SystemFileSection(MiddlewareBase):
    """把一段文本插到系统提示词最前。

    ``on_system_prompt`` 与其余 hook 不同 —— **没有 ``next_handler``**,
    是纯变换(§5.5)。而且必须是 ``async def``,同步实现会被静默跳过。
    """

    def __init__(self, text: str) -> None:
        self.text = text

    async def on_system_prompt(self, agent: Agent, current_prompt: str) -> str:
        if not current_prompt:
            return self.text
        return f"{self.text}\n\n{current_prompt}"


async def setup(agent: Agent, options) -> Effect:
    if options.system_prompt is not None:            # --system 内联优先
        text = options.system_prompt
    elif options.system_prompt_file.is_file():
        text = options.system_prompt_file.read_text(encoding="utf-8")
    else:
        return None                                  # 都没有 → 什么都不做

    text = text.strip()
    if not text:
        return None

    section = SystemFileSection(text)
    agent._system_prompt_middlewares.append(section)

    def cleanup() -> None:
        try:
            agent._system_prompt_middlewares.remove(section)
        except ValueError:
            pass

    return cleanup
