"""e2e 夹具。

``agent/`` 是**模板**,测试跑在它的副本上 —— 插件会往自己目录写日志
(``lifecycle.log`` / ``reports.log``),原地跑会把夹具弄脏,而且上一次运行的
残留会让下一次的断言飘。

除 ``e2e`` 外,其余夹具都把模板的一个子目录搬成 ``plugins/``。原因:
``./plugins`` 恒在插件目录列表末尾,主夹具那 13 个插件会跟着加载进来,把
故障矩阵、优先级这类断言搅浑。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from helpers import Project, copy_tree

#: 模板目录(含 SYSTEM.md / plugins/ / skills/ / faulty/ / shared/ ...)。
TEMPLATE = Path(__file__).parent / "agent"


@pytest.fixture
def e2e(tmp_path: Path) -> Project:
    """主夹具的副本 —— 插件目录是 ``plugins/``,13 个插件(12 LOADED + 1 SKIPPED)。"""
    return Project(copy_tree(TEMPLATE, tmp_path / "agent"))


@pytest.fixture
def faulty(tmp_path: Path) -> Project:
    """故障夹具。``faulty/`` 直接当 ``plugins/``,不掺主插件。"""
    root = tmp_path / "faulty-agent"
    (root / "plugins").parent.mkdir(parents=True, exist_ok=True)
    copy_tree(TEMPLATE / "faulty", root / "plugins")
    return Project(root)


@pytest.fixture
def optional(tmp_path: Path) -> Project:
    """可选依赖夹具。"""
    root = tmp_path / "optional-agent"
    root.mkdir(parents=True, exist_ok=True)
    copy_tree(TEMPLATE / "optional", root / "plugins")
    return Project(root)


@pytest.fixture
def shared(tmp_path: Path) -> Project:
    """两个插件目录 —— 用来测优先级与覆盖。

    返回的 Project 里 ``shared/base`` 与 ``shared/biz`` 是两个插件目录,
    ``plugins/`` 是空的。测试自己把它们按顺序传给 ``--plugins``。
    """
    root = tmp_path / "shared-agent"
    root.mkdir(parents=True, exist_ok=True)
    copy_tree(TEMPLATE / "shared", root / "shared")
    (root / "plugins").mkdir(parents=True, exist_ok=True)
    return Project(root)


@pytest.fixture
def bare(tmp_path: Path) -> Project:
    """空项目 —— 只有空的 ``plugins/``,测试自己往里写。"""
    return Project(tmp_path / "bare")


@pytest.fixture
def base_dir(shared: Project) -> Path:
    return shared.root / "shared" / "base"


@pytest.fixture
def biz_dir(shared: Project) -> Path:
    return shared.root / "shared" / "biz"
