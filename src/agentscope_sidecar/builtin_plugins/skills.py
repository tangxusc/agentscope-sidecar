"""内置插件:技能目录(设计文档 §7.2)。

读 ``./skills`` 与 ``--skills`` 给的额外目录,挂到 ``agent.toolkit`` 上。

**``Toolkit`` 没有公开的 ``add_skill``** —— 技能只能通过构造参数
``skills_or_loaders`` 传入,最终落到 ``tool_groups[0].skills_or_loaders``
(一个普通 list)。构造后追加就是改它。

必须用 ``basic`` 组:它是默认激活的,新建的组默认不激活,技能说明不会出现。
"""

from __future__ import annotations

from agentscope.agent import Agent
from agentscope.skill import LocalSkillLoader

from agentscope_sidecar.effects import Effect

#: 技能挂在哪个 tool group 上。``basic`` 是 AgentScope 默认激活的那个。
SKILL_GROUP = "basic"


async def setup(agent: Agent, options) -> Effect:
    dirs = [options.skills_dir, *options.extra_skills]
    existing = [p for p in dirs if p.is_dir()]
    if not existing:
        return None                                  # 一个都没有 → 什么都不做

    group = _basic_group(agent)
    loaders = [
        LocalSkillLoader(str(p), scan_subdir=True) for p in existing
    ]
    group.skills_or_loaders.extend(loaders)

    def cleanup() -> None:
        for loader in loaders:
            try:
                group.skills_or_loaders.remove(loader)
            except ValueError:
                pass

    return cleanup


def _basic_group(agent: Agent):
    for group in agent.toolkit.tool_groups:
        if group.name == SKILL_GROUP:
            return group
    raise RuntimeError(                               # pragma: no cover
        f"toolkit 里没有 {SKILL_GROUP!r} 组;AgentScope 的默认分组变了",
    )
