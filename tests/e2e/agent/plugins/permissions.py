"""往 Agent 的权限上下文里加规则。

两个容易踩的点:

1. 规则的 key 是**工具名**,不是插件名。写成插件名(``danger``)规则永远匹配不上,
   而且不会有任何提示 —— 引擎找不到规则就按模式默认值走。
2. ``rule_content`` 只对**自己重写了 ``match_rule`` 的工具**有效(Bash / Read /
   Write / Edit / Glob / Grep)。自定义函数工具的 ``match_rule`` 对非空内容一律返回
   ``False``,所以只能写 ``rule_content=None`` —— 那是工具名级别的规则,匹配每一次
   调用。
"""

from __future__ import annotations

from agentscope.agent import Agent
from agentscope.permission import PermissionBehavior, PermissionRule


def _rule(tool: str, behavior: PermissionBehavior) -> PermissionRule:
    return PermissionRule(
        tool_name=tool, rule_content=None, behavior=behavior, source="permissions",
    )


async def setup(agent: Agent) -> None:
    context = agent.state.permission_context
    context.deny_rules.setdefault("wipe", []).append(
        _rule("wipe", PermissionBehavior.DENY),
    )
    context.ask_rules.setdefault("make_report", []).append(
        _rule("make_report", PermissionBehavior.ASK),
    )

    assert context.mode is not None
