"""内置插件:system_prompt 与 skills(设计文档 §7)。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from helpers import Project

from agentscope_sidecar.loader import PluginState


# ------------------------------------------------------------------ system_prompt


def test_system_md_reaches_the_prompt(project: Project) -> None:
    project.system("# 我的 Agent\n\n回答要简短。\n")
    sidecar = project.boot()
    try:
        prompt = asyncio.run(sidecar.build_agent()._get_system_prompt())
        assert "我的 Agent" in prompt
        assert "回答要简短" in prompt
    finally:
        sidecar.shutdown()


def test_missing_system_md_is_silent(project: Project) -> None:
    sidecar = project.boot()
    try:
        agent = sidecar.build_agent()
        assert agent._system_prompt_middlewares == []
        assert asyncio.run(agent._get_system_prompt()) == ""
        assert sidecar.loader.records["system_prompt"].state is PluginState.LOADED
    finally:
        sidecar.shutdown()


def test_empty_system_md_is_skipped(project: Project) -> None:
    project.system("   \n\n  \n")
    sidecar = project.boot()
    try:
        assert sidecar.build_agent()._system_prompt_middlewares == []
    finally:
        sidecar.shutdown()


def test_inline_system_wins_over_file(project: Project) -> None:
    project.system("# 来自文件\n")
    sidecar = project.boot(system="# 来自命令行\n")
    try:
        prompt = asyncio.run(sidecar.build_agent()._get_system_prompt())
        assert "来自命令行" in prompt
        assert "来自文件" not in prompt
    finally:
        sidecar.shutdown()


def test_system_file_flag(project: Project) -> None:
    project.write("custom/PROMPT.md", "# 自定义文件\n")
    sidecar = project.boot(system_file="./custom/PROMPT.md")
    try:
        prompt = asyncio.run(sidecar.build_agent()._get_system_prompt())
        assert "自定义文件" in prompt
    finally:
        sidecar.shutdown()


def test_system_md_goes_first(project: Project) -> None:
    """``SYSTEM.md`` 放最前,插件片段跟在其后。"""
    project.system("# 基础\n")
    project.plugin("extra", '''
from agentscope.middleware import MiddlewareBase


class Extra(MiddlewareBase):
    async def on_system_prompt(self, agent, current_prompt: str) -> str:
        return f"{current_prompt}\\n\\n# 插件片段"


async def setup(agent):
    agent._system_prompt_middlewares.append(Extra())
''')
    sidecar = project.boot()
    try:
        prompt = asyncio.run(sidecar.build_agent()._get_system_prompt())
        assert prompt.index("基础") < prompt.index("插件片段")
    finally:
        sidecar.shutdown()


def test_system_prompt_cleanup_removes_section(project: Project) -> None:
    project.system("# 基础\n")
    sidecar = project.boot()
    try:
        assert len(sidecar.build_agent()._system_prompt_middlewares) == 1
        asyncio.run(sidecar.unload("system_prompt"))
        assert sidecar.build_agent()._system_prompt_middlewares == []
    finally:
        sidecar.shutdown()


# ------------------------------------------------------------------ skills


def test_skills_dir_is_registered(project: Project) -> None:
    project.skill("deploy", "部署流程", "# 部署\n\n按步骤来。")
    sidecar = project.boot()
    try:
        toolkit = sidecar.build_agent().toolkit
        text = asyncio.run(toolkit.get_skill_instructions())
        assert "deploy" in (text or "")
    finally:
        sidecar.shutdown()


def test_missing_skills_dir_is_silent(project: Project) -> None:
    sidecar = project.boot()
    try:
        group = sidecar.build_agent().toolkit.tool_groups[0]
        assert group.skills_or_loaders == []
        assert sidecar.loader.records["skills"].state is PluginState.LOADED
    finally:
        sidecar.shutdown()


def test_extra_skills_dirs_replace_default(project: Project) -> None:
    """``--skills`` 是**替换** ``./skills``,不是追加 —— 与 ``--system-file`` 一致。

    想同时用两个就都列出来:``--skills ./skills --skills ./more-skills``。
    """
    project.skill("from_default", "默认目录", "# 默认")
    extra = project.root / "more-skills"
    project.write(
        "more-skills/from_extra/SKILL.md",
        "---\nname: from_extra\ndescription: 额外目录\n---\n\n# 额外\n",
    )
    sidecar = project.boot(skills=(str(extra),))
    try:
        text = asyncio.run(sidecar.build_agent().toolkit.get_skill_instructions())
        assert "from_extra" in (text or "")
        assert "from_default" not in (text or "")
    finally:
        sidecar.shutdown()


def test_both_skill_dirs_when_both_listed(project: Project) -> None:
    project.skill("from_default", "默认目录", "# 默认")
    extra = project.root / "more-skills"
    project.write(
        "more-skills/from_extra/SKILL.md",
        "---\nname: from_extra\ndescription: 额外目录\n---\n\n# 额外\n",
    )
    sidecar = project.boot(
        skills=(str(project.root / "skills"), str(extra)),
    )
    try:
        text = asyncio.run(sidecar.build_agent().toolkit.get_skill_instructions())
        assert "from_default" in (text or "")
        assert "from_extra" in (text or "")
    finally:
        sidecar.shutdown()


def test_skills_attach_to_basic_group(project: Project) -> None:
    """必须挂 ``basic`` 组 —— 它是默认激活的,别的组技能说明不出现。"""
    project.skill("deploy", "部署", "# 部署")
    sidecar = project.boot()
    try:
        groups = sidecar.build_agent().toolkit.tool_groups
        basic = next(g for g in groups if g.name == "basic")
        assert len(basic.skills_or_loaders) == 1
    finally:
        sidecar.shutdown()


def test_skills_cleanup(project: Project) -> None:
    project.skill("deploy", "部署", "# 部署")
    sidecar = project.boot()
    try:
        asyncio.run(sidecar.unload("skills"))
        group = sidecar.build_agent().toolkit.tool_groups[0]
        assert group.skills_or_loaders == []
    finally:
        sidecar.shutdown()


def test_skills_can_be_overridden_by_user_plugin(project: Project) -> None:
    """内置插件优先级最低 —— 用户放个同名插件就顶掉它。"""
    project.plugin("skills", "async def setup():\n    pass\n")
    project.skill("deploy", "部署", "# 部署")
    sidecar = project.boot()
    try:
        group = sidecar.build_agent().toolkit.tool_groups[0]
        assert group.skills_or_loaders == []      # 内置那个没跑
    finally:
        sidecar.shutdown()
