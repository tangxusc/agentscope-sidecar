"""文件监听(设计文档 §11.9)。

用**标准库轮询**实现,不引入新依赖 —— 插件目录通常只有几十个文件,1 秒一轮的
``stat`` 开销可以忽略。若环境里装了 ``watchfiles``,自动改用它(更快、基于
inotify/FSEvents)。
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

logger = logging.getLogger("sidecar.reload")

#: 这些名字不参与监听:解释器产物、编辑器临时文件、`_` 前缀的私有内容。
IGNORED_DIRS = {"__pycache__", ".git", ".idea", ".vscode", "node_modules"}
IGNORED_SUFFIXES = (".pyc", ".pyo", ".swp", ".swx", "~")


@dataclass(frozen=True)
class Stamp:
    mtime: float
    size: int


def _iter_files(plugin_dirs: Iterable[Path]) -> Iterable[Path]:
    for root in plugin_dirs:
        if not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [
                d for d in dirnames
                if d not in IGNORED_DIRS and not d.startswith(".")
            ]
            base = Path(dirpath)
            for name in filenames:
                if name.endswith(IGNORED_SUFFIXES) or name.startswith("."):
                    continue
                yield base / name


def snapshot(plugin_dirs: Iterable[Path]) -> dict[Path, Stamp]:
    """给插件目录拍一份 (mtime, size) 快照。"""
    stamps: dict[Path, Stamp] = {}
    for path in _iter_files(plugin_dirs):
        try:
            stat = path.stat()
        except OSError:
            continue
        stamps[path] = Stamp(mtime=stat.st_mtime, size=stat.st_size)
    return stamps


def diff(before: dict[Path, Stamp], after: dict[Path, Stamp]) -> list[str]:
    """返回变化的可读描述;空列表表示没变。"""
    changes: list[str] = []
    for path in sorted(set(after) - set(before)):
        changes.append(f"新增 {path.name}")
    for path in sorted(set(before) - set(after)):
        changes.append(f"删除 {path.name}")
    for path in sorted(set(before) & set(after)):
        if before[path] != after[path]:
            changes.append(f"修改 {path.name}")
    return changes


class PollingWatcher:
    """轮询监听器。每 ``interval`` 秒比对一次快照。"""

    def __init__(
        self,
        plugin_dirs: Iterable[Path],
        *,
        interval: float = 1.0,
    ) -> None:
        self.plugin_dirs = list(plugin_dirs)
        self.interval = interval

    async def run(self, on_change: Callable[[list[str]], object]) -> None:
        before = snapshot(self.plugin_dirs)
        while True:
            await asyncio.sleep(self.interval)
            after = snapshot(self.plugin_dirs)
            changes = diff(before, after)
            if not changes:
                continue
            before = after
            result = on_change(changes)
            if asyncio.iscoroutine(result):
                await result


def watchfiles_available() -> bool:
    try:
        import watchfiles  # noqa: F401
    except ImportError:
        return False
    return True


async def watch(
    plugin_dirs: Iterable[Path],
    on_change: Callable[[list[str]], object],
    *,
    interval: float = 1.0,
) -> None:
    """监听插件目录。有 ``watchfiles`` 就用它,否则轮询。"""
    dirs = [d for d in plugin_dirs if d.is_dir()]

    if watchfiles_available():                       # pragma: no cover - 环境相关
        import watchfiles

        async for raw in watchfiles.awatch(*dirs):
            changes = [f"{change.name.lower()} {Path(path).name}"
                       for change, path in raw]
            result = on_change(changes)
            if asyncio.iscoroutine(result):
                await result
        return

    await PollingWatcher(dirs, interval=interval).run(on_change)
