"""pytest 夹具与临时目录选择。"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from helpers import Project


@pytest.fixture
def project(tmp_path: Path) -> Project:
    return Project(tmp_path / "proj")


def _candidates(config: pytest.Config) -> list[Path]:
    """basetemp 的候选位置,按优先级。

    首选系统临时目录 —— 不放工作区有两个理由:仓库里不留产物,而且受限环境
    对工作区内的批量删除有守卫,测试目录一攒大就会把运行打断。
    """
    return [
        Path(tempfile.gettempdir()) / "agentscope-sidecar-pytest",
        Path("/tmp") / "agentscope-sidecar-pytest",
        config.rootpath / ".pytest-tmp",
    ]


def pytest_configure(config: pytest.Config) -> None:
    """挑一个可写的 basetemp。``addopts`` 里不写死,因为路径要探测。"""
    if config.option.basetemp:
        return

    for candidate in _candidates(config):
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / ".write-probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError:
            continue
        config.option.basetemp = str(candidate)
        return


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """跑完删掉 basetemp。

    正常情况下它在系统临时目录里,留着也无所谓;pytest 下次运行本来就会重建。
    这条只在退回工作区时才有实际意义 —— 那种情况下不清理会一直攒。
    """
    raw = session.config.option.basetemp
    if not raw:
        return

    base = Path(raw)
    if not base.is_absolute():
        base = session.config.rootpath / base

    try:
        base.resolve().relative_to(session.config.rootpath.resolve())
    except ValueError:
        return                                     # 不在仓库内,交给系统清

    shutil.rmtree(base, ignore_errors=True)
