"""事件目录:文档与代码必须一致。

事件名是**纯字符串**,没有任何静态检查能发现拼错、漏发或漏写文档。设计文档 §6.6
是唯一的事实来源,这里比对三件事:

1. 代码里 ``emit("...")`` 的每个名字都在文档的目录表里;
2. 文档表里的每个名字都在代码里真的被发过;
3. ``Telemetry`` 订阅的每个名字都真的会发出 —— 订阅一个永不触发的名字,计数永远
   是 0,看起来像功能没生效。
"""

from __future__ import annotations

import re
from pathlib import Path

from agentscope_sidecar.services.telemetry import AGENT_EVENT, LIFECYCLE_EVENTS

SRC = Path(__file__).parent.parent / "src" / "agentscope_sidecar"
DESIGN = Path(__file__).parent.parent / "docs" / "design.md"

#: 代码里事件名写成双引号字符串。
CODE_PATTERN = re.compile(r'"((?:sidecar|server|agent|rpc)/[\w-]+)"')

#: 文档表格里事件名写在反引号里。
DOC_PATTERN = re.compile(r"`((?:sidecar|server|agent|rpc)/[\w-]+)`")

#: 文档里刻意提到但不由底座发出的名字(测试夹具、示例)。留空表示一个都没有。
EXTERNAL: set[str] = set()


def _emitted() -> set[str]:
    """代码里 ``emit(...)`` / 常量赋值里出现的全部事件名。"""
    names: set[str] = set()
    for path in SRC.rglob("*.py"):
        names |= set(CODE_PATTERN.findall(path.read_text(encoding="utf-8")))
    return names


def _documented() -> set[str]:
    """文档 §6.6 事件目录表里的名字。"""
    text = DESIGN.read_text(encoding="utf-8")
    section = text[text.index("### 6.6 事件目录"):text.index("## 7.")]
    return set(DOC_PATTERN.findall(section))


def test_documented_events_are_actually_emitted() -> None:
    """文档写了但代码没发 —— 用户订阅了收不到。"""
    missing = _documented() - _emitted() - EXTERNAL
    assert not missing, f"文档里有但代码没发:{sorted(missing)}"


def test_emitted_events_are_documented() -> None:
    """代码发了但文档没写 —— 用户不知道能订阅什么。"""
    undocumented = _emitted() - _documented()
    assert not undocumented, f"代码里有但文档没写:{sorted(undocumented)}"


def test_telemetry_only_subscribes_to_real_events() -> None:
    """订阅一个永不触发的名字,计数永远是 0,看着像功能没生效。"""
    subscribed = set(LIFECYCLE_EVENTS) | {AGENT_EVENT}
    assert subscribed <= _emitted(), (
        f"Telemetry 订阅了不会发出的事件:{sorted(subscribed - _emitted())}"
    )


def test_lifecycle_events_are_all_sidecar_prefixed() -> None:
    """生命周期事件都在 ``sidecar/`` 命名空间下 —— 好筛。"""
    assert all(name.startswith("sidecar/") for name in LIFECYCLE_EVENTS)


def test_agent_event_is_the_single_agent_entry() -> None:
    """agent 循环事件只走 ``agent/event`` 一个入口,靠 ``type`` 区分。

    为每种事件类型单开一个总线事件会让事件名归一逻辑散到多处 —— 那份逻辑曾经
    写了两份并立刻漂移。
    """
    agent_events = {n for n in _emitted() if n.startswith("agent/")}
    assert agent_events == {AGENT_EVENT}
