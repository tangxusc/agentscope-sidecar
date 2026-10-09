"""文档与代码的引用一致性。

源码与测试的注释里到处写 ``(设计文档 §8.4)``。这些引用**没有任何静态检查能发现
失效** —— 章节一改编号,注释就指向不存在的段落,而读的人只会以为自己看错了。

这里把「文档 ↔ 代码」的核对变成永久的:文档里每个 ``§X.Y`` 都必须真的有对应标题,
反过来源码与测试里的每个引用都必须落在文档里。
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
DESIGN = ROOT / "docs" / "design.md"
SRC = ROOT / "src"
TESTS = ROOT / "tests"

#: 章节号:``## 8. 归属追踪`` / ``### 8.4 回滚``
HEADING = re.compile(r"^#{2,4} (\d+(?:\.\d+)*)[. ]", re.M)
#: 引用:``(设计文档 §8.4)``
REFERENCE = re.compile(r"§(\d+(?:\.\d+)*)")

#: 核心模块 —— 文件头必须写出处,否则读者不知道去哪找规格。
CORE_MODULES = (
    "app.py", "cli.py", "context.py", "convention.py", "discover.py",
    "effects.py", "errors.py", "events.py", "inject.py", "loader.py",
    "models.py", "mock_model.py", "reload.py",
)


def _headings() -> set[str]:
    """文档里的章节号。父节点也算存在 —— ``§8`` 指向 ``## 8.``。"""
    numbers = set(HEADING.findall(DESIGN.read_text(encoding="utf-8")))
    return {
        ".".join(number.split(".")[:depth])
        for number in numbers
        for depth in range(1, len(number.split(".")) + 1)
    }


def _references() -> list[tuple[str, str]]:
    """源码与测试里的全部 ``§`` 引用,``(文件, 章节号)``。"""
    found: list[tuple[str, str]] = []
    for root in (SRC, TESTS):
        for path in sorted(root.rglob("*.py")):
            rel = str(path.relative_to(ROOT))
            found.extend(
                (rel, number)
                for number in REFERENCE.findall(path.read_text(encoding="utf-8"))
            )
    return found


def test_every_reference_points_at_a_real_section() -> None:
    known = _headings()
    dangling = [
        (path, number) for path, number in _references()
        if number not in known
    ]
    assert not dangling, f"引用了不存在的章节:{dangling}"


def test_design_doc_section_numbers_have_no_gaps() -> None:
    """同级编号必须连续。

    改编号时最容易漏掉中间某一段 —— ``§11.6`` 之后直接跳到 ``§11.8``,
    看上去很正常,直到有人去找那个不存在的 ``§11.7``。
    """
    numbers = sorted(
        {number for number in HEADING.findall(DESIGN.read_text(encoding="utf-8"))},
        key=lambda s: [int(x) for x in s.split(".")],
    )
    children: dict[str, list[int]] = {}
    for number in numbers:
        parts = number.split(".")
        children.setdefault(".".join(parts[:-1]), []).append(int(parts[-1]))

    for parent, used in children.items():
        assert used == list(range(1, len(used) + 1)), (
            f"§{parent or '(顶层)'} 的子章节编号不连续:{used}"
        )


def test_no_retired_document_names() -> None:
    """文档只有一个名字:``docs/design.md``。注释里不该再出现「设计 v2」之类。"""
    offenders = [
        str(path.relative_to(ROOT))
        for root in (SRC, TESTS)
        for path in sorted(root.rglob("*.py"))
        if path.name != __file__.rsplit("/", 1)[-1]      # 本文件自己要写这个词
        and "设计 v2" in path.read_text(encoding="utf-8")
    ]
    assert not offenders, f"仍写着「设计 v2」:{offenders}"


def test_every_core_module_points_at_the_design_doc() -> None:
    """核心模块的文件头都该有一行出处 —— 没有它,读者不知道去哪找规格。"""
    missing = [
        name for name in CORE_MODULES
        if "设计文档" not in (SRC / "agentscope_sidecar" / name).read_text(
            encoding="utf-8",
        )
    ]
    assert not missing, f"这些模块的文件头没写出处:{missing}"


def test_design_doc_is_not_empty() -> None:
    """文档被整体替换或清空时,上面的检查会「通过」得毫无意义。"""
    text = DESIGN.read_text(encoding="utf-8")
    assert len(text) > 20_000
    assert len(HEADING.findall(text)) >= 40
