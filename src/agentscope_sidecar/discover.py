"""发现与导入(设计文档 §4.1)。

扫描是**非递归**的:只看每个插件目录的顶层。目录形态的插件内部文件一律是
私有配套文件,不参与扫描。

**没有声明收集。** 插件的唯一入口是 ``setup``(§4.2),底座不再扫描模块属性。
"""

from __future__ import annotations

import importlib.util
import keyword
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Sequence

from .errors import PluginImportError

SKIP_NAMES = {"__pycache__"}

#: 插件模块在 sys.modules 里的前缀。重载时按它整段清理。
MODULE_PREFIX = "sidecar_plugin_"


@dataclass(frozen=True)
class PluginSource:
    """一个待加载的插件。"""

    path: Path            # __init__.py 或 <name>.py
    root: Path            # 来源插件目录
    is_package: bool
    plugin_id: str        # 来源目录下的顶层名字,单段
    order: int            # 目录在 plugin_dirs 中的下标;越大优先级越高

    @property
    def plugin_dir(self) -> Path:
        """插件自身所在目录(目录形态时为包目录)。"""
        return self.path.parent


@dataclass
class ScanResult:
    sources: list[PluginSource] = field(default_factory=list)
    #: 被覆盖的插件:**(落败者, 胜出者)**,存 ``PluginSource`` 而不是路径。
    #:
    #: 存路径的话算不出插件目录根 —— 目录形态时 ``path.parent`` 是包目录,
    #: 不在 ``plugin_dirs`` 里,``inspect`` 的优先级序号会退成 -1,一个用户插件
    #: 被显示成 ``<builtin>``。
    shadowed: list[tuple[PluginSource, PluginSource]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def scan(plugin_dirs: Sequence[Path]) -> ScanResult:
    """按优先级升序扫描多个插件目录。

    ``plugin_dirs[0]`` 优先级最低,最后一个最高。同 id 时高优先级覆盖低优先级,
    被覆盖者不进入 ``sources``,只记在 ``shadowed`` 里。
    """
    winners: dict[str, PluginSource] = {}
    result = ScanResult()
    scanned = 0

    for order, root in enumerate(plugin_dirs):
        if not root.is_dir():
            continue
        scanned += 1
        try:
            entries = sorted(root.iterdir())
        except OSError as exc:
            result.warnings.append(f"插件目录不可读,已跳过:{root}({exc})")
            continue
        for entry in entries:
            found = _classify(entry, root, order, result.warnings)
            if found is None:
                continue
            previous = winners.get(found.plugin_id)
            if previous is not None:
                result.shadowed.append((previous, found))
            winners[found.plugin_id] = found

    if scanned == 0:
        result.warnings.append(
            f"没有任何插件目录存在:{[str(d) for d in plugin_dirs]}",
        )

    result.sources = sorted(winners.values(), key=lambda s: (s.order, s.plugin_id))
    return result


def _classify(
    entry: Path, root: Path, order: int, warnings: list[str],
) -> PluginSource | None:
    """判定插件目录顶层的一个条目。返回 None 表示不是插件。"""
    name = entry.name

    if name.startswith("_"):                       # 移出插件集(§4.1)
        return None
    if name in SKIP_NAMES or name.endswith(".pyc"):
        return None

    if entry.is_file():
        if entry.suffix != ".py":
            return None                            # README、数据文件等
        plugin_id, is_package, path = entry.stem, False, entry
    elif entry.is_dir():
        if not (entry / "__init__.py").is_file():
            try:
                has_py = any(entry.glob("*.py"))
            except OSError:
                has_py = False
            if has_py:
                warnings.append(
                    f"{entry} 含 .py 文件但没有 __init__.py,已跳过。可能的原因:"
                    f"(a) 目录形态插件忘了写 __init__.py;"
                    f"(b) 它不是插件,应加 _ 前缀或移出插件目录;"
                    f"(c) 插件目录指错了层级 —— 若这是插件分组目录(如仓库根),"
                    f"请把它下面的具体目录逐个列进 plugin_dirs。"
                )
            return None                            # 纯资源目录静默忽略
        plugin_id, is_package, path = name, True, entry / "__init__.py"
    else:
        return None

    if not valid_plugin_name(plugin_id):
        warnings.append(invalid_name_warning(entry, plugin_id))
        return None

    return PluginSource(path, root, is_package, plugin_id, order)


def valid_plugin_name(name: str) -> bool:
    """插件名必须是合法的 Python 标识符,且不是关键字。

    **不是风格要求,是正确性要求。** ``module_key()`` 用 ``\\W -> _`` 归一化,
    于是 ``my-plugin`` / ``my_plugin`` / ``my.plugin`` 会折叠成同一个模块键 ——
    两个插件共用一份模块对象,后者的模块覆盖前者,``_modules`` 里只剩一个。
    两边都报 ``loaded``,但有一个的模块对象已经丢了。

    插件名还会进入 ``ctx.logger`` 的名字与服务名,含 ``-`` 的 id 在那里也站不住。
    """
    return name.isidentifier() and not keyword.iskeyword(name)


def invalid_name_warning(entry: Path, plugin_id: str) -> str:
    """非法插件名的告警文本。扫描与 ``-e`` 共用 —— 两处说法不一致会让人以为是
    两个不同的问题。"""
    return (
        f"{entry} 的插件名 {plugin_id!r} 不是合法的 Python 标识符,已跳过。"
        f"插件文件就是 Python 模块,名字要能当模块名用 —— "
        f"`my-plugin.py` / `my.plugin.py` / `123abc.py` / `class.py` 都不行,"
        f"用 `my_plugin.py`。"
    )


def module_key(plugin_id: str) -> str:
    """插件模块在 sys.modules 中的键名。"""
    return MODULE_PREFIX + re.sub(r"\W", "_", plugin_id)


def import_module(source: PluginSource) -> ModuleType:
    """按文件路径导入插件模块。

    目录形态设置 ``submodule_search_locations``,否则插件内的相对导入失败。
    """
    key = module_key(source.plugin_id)
    if source.is_package:
        spec = importlib.util.spec_from_file_location(
            key, source.path,
            submodule_search_locations=[str(source.path.parent)],
        )
    else:
        spec = importlib.util.spec_from_file_location(key, source.path)

    if spec is None or spec.loader is None:
        raise PluginImportError(f"无法为 {source.path} 构造导入规格")

    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        sys.modules.pop(key, None)                 # 失败清理,避免污染
        # 带上原始异常类型 —— 只写 str(exc) 会让「模块里写错了」看起来和
        # 「语法错误」一样
        raise PluginImportError(
            f"导入 {source.path} 失败:{type(exc).__name__}: {exc}",
        ) from exc
    return module


__all__ = [
    "MODULE_PREFIX",
    "SKIP_NAMES",
    "PluginSource",
    "ScanResult",
    "import_module",
    "module_key",
    "invalid_name_warning",
    "scan",
    "valid_plugin_name",
]
