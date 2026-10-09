"""扫描、插件 id、导入(设计文档 §4.1)。"""

from __future__ import annotations

from pathlib import Path

import pytest

from helpers import Project

from agentscope_sidecar.discover import import_module, module_key, scan
from agentscope_sidecar.errors import PluginImportError


def ids(result) -> list[str]:
    return [s.plugin_id for s in result.sources]


# ------------------------------------------------------------------ 扫描


def test_single_file_and_package_forms(project: Project) -> None:
    project.plugin("single", "x = 1\n")
    project.plugin_pkg("packaged", "x = 1\n")
    assert ids(scan([project.root / "plugins"])) == ["packaged", "single"]


def test_scan_is_not_recursive(project: Project) -> None:
    """目录形态的内部文件不是插件。"""
    project.plugin_pkg("outer", "x = 1\n", inner="y = 1\n")
    result = scan([project.root / "plugins"])
    assert ids(result) == ["outer"]


def test_plugin_id_is_single_segment(project: Project) -> None:
    project.plugin_pkg("audit", "x = 1\n")
    assert all("." not in i for i in ids(scan([project.root / "plugins"])))


def test_underscore_prefix_excluded(project: Project) -> None:
    project.plugin("keep", "x = 1\n")
    project.plugin("_wip", "x = 1\n")
    (project.root / "plugins" / "_archive").mkdir()
    project.write("plugins/_archive/old.py", "x = 1\n")
    assert ids(scan([project.root / "plugins"])) == ["keep"]


def test_pycache_and_pyc_excluded(project: Project) -> None:
    project.plugin("keep", "x = 1\n")
    (project.root / "plugins" / "__pycache__").mkdir()
    project.write("plugins/keep.pyc", "x")
    assert ids(scan([project.root / "plugins"])) == ["keep"]


def test_non_python_files_ignored(project: Project) -> None:
    project.plugin("keep", "x = 1\n")
    project.write("plugins/README.md", "# 说明\n")
    project.write("plugins/data.json", "{}\n")
    result = scan([project.root / "plugins"])
    assert ids(result) == ["keep"]
    assert result.warnings == []


def test_resource_only_directory_is_silent(project: Project) -> None:
    project.plugin("keep", "x = 1\n")
    (project.root / "plugins" / "assets").mkdir()
    project.write("plugins/assets/logo.svg", "<svg/>")
    result = scan([project.root / "plugins"])
    assert ids(result) == ["keep"]
    assert result.warnings == []


def test_directory_with_py_but_no_init_warns(project: Project) -> None:
    (project.root / "plugins" / "forgot").mkdir(parents=True)
    project.write("plugins/forgot/code.py", "x = 1\n")
    result = scan([project.root / "plugins"])
    assert ids(result) == []
    assert len(result.warnings) == 1
    assert "__init__.py" in result.warnings[0]


def test_missing_dirs_are_silent(project: Project, tmp_path: Path) -> None:
    result = scan([tmp_path / "nope", project.root / "plugins"])
    assert result.sources == []
    assert result.warnings == []


def test_scan_order_is_deterministic(project: Project) -> None:
    for name in ("c", "a", "b"):
        project.plugin(name, "x = 1\n")
    assert ids(scan([project.root / "plugins"])) == ["a", "b", "c"]


def test_later_dir_wins_and_records_shadowed(project: Project) -> None:
    low = project.plugin_dir("low")
    high = project.plugin_dir("high")
    project.write_in(low, "same.py", "x = 1\n")
    project.write_in(high, "same.py", "x = 2\n")

    result = scan([low, high])
    assert len(result.sources) == 1
    assert result.sources[0].root == high
    loser, winner = result.shadowed[0]
    assert loser.plugin_id == "same"
    assert loser.path == low / "same.py"
    assert loser.root == low
    assert winner.path == high / "same.py"


def test_three_way_shadow_records_both_losers(project: Project) -> None:
    dirs = [project.plugin_dir(n) for n in ("one", "two", "three")]
    for d in dirs:
        project.write_in(d, "same.py", "x = 1\n")

    result = scan(dirs)
    assert len(result.sources) == 1
    assert result.sources[0].root == dirs[-1]
    assert len(result.shadowed) == 2


def test_package_plugin_dir_property(project: Project) -> None:
    project.plugin_pkg("pkg", "x = 1\n")
    source = scan([project.root / "plugins"]).sources[0]
    assert source.is_package is True
    assert source.plugin_dir == project.root / "plugins" / "pkg"


# ------------------------------------------------------------------ 命名校验


def test_invalid_names_are_skipped_with_warning(project: Project) -> None:
    project.write("plugins/my-plugin.py", "x = 1\n")
    project.write("plugins/123abc.py", "x = 1\n")
    project.write("plugins/class.py", "x = 1\n")
    project.plugin("keep", "x = 1\n")

    result = scan([project.root / "plugins"])
    assert ids(result) == ["keep"]
    assert len(result.warnings) == 3
    for warning in result.warnings:
        assert "不是合法的 Python 标识符" in warning


def test_uppercase_name_is_valid(project: Project) -> None:
    """大写合法,只是不符合 PEP 8。

    **单独一个测试**:macOS 文件系统大小写不敏感,``Weather.py`` 与
    ``weather.py`` 是同一个文件,放一起会互相覆盖。
    """
    project.plugin("Weather", "x = 1\n")
    assert ids(scan([project.root / "plugins"])) == ["Weather"]


def test_package_name_checked_too(project: Project) -> None:
    project.write("plugins/my-pkg/__init__.py", "x = 1\n")
    project.plugin_pkg("good_pkg", "x = 1\n")

    result = scan([project.root / "plugins"])
    assert ids(result) == ["good_pkg"]
    assert len(result.warnings) == 1
    assert "my-pkg" in result.warnings[0]


def test_underscore_prefixed_invalid_name_is_silent(project: Project) -> None:
    """``_`` 前缀先判定 —— 停用的插件不该再报名字问题。"""
    project.write("plugins/_my-plugin.py", "x = 1\n")
    result = scan([project.root / "plugins"])
    assert ids(result) == []
    assert result.warnings == []


def test_colliding_names_share_a_module_key() -> None:
    """这就是要拦住的根因:归一化后三个名字折叠成同一个模块键。"""
    keys = {module_key(n) for n in ("my-plugin", "my_plugin", "my.plugin")}
    assert len(keys) == 1


def test_module_key_prefix() -> None:
    assert module_key("weather") == "sidecar_plugin_weather"


# ------------------------------------------------------------------ 导入


def test_import_module_executes_top_level(project: Project) -> None:
    # 用绝对路径 —— 插件里的相对路径按进程 CWD 解析,会漏到仓库根
    marker = project.root / "ran.txt"
    project.plugin(
        "runs",
        f"from pathlib import Path\nPath(r'{marker}').write_text('1')\n",
    )
    source = scan([project.root / "plugins"]).sources[0]
    import_module(source)
    assert marker.is_file()


def test_import_failure_raises_with_type(project: Project) -> None:
    project.plugin("broken", "raise ValueError('导入就炸')\n")
    source = scan([project.root / "plugins"]).sources[0]
    with pytest.raises(PluginImportError) as exc:
        import_module(source)
    assert "ValueError" in str(exc.value)


def test_import_failure_cleans_sys_modules(project: Project) -> None:
    import sys

    project.plugin("broken", "raise RuntimeError('x')\n")
    source = scan([project.root / "plugins"]).sources[0]
    with pytest.raises(PluginImportError):
        import_module(source)
    assert module_key("broken") not in sys.modules


def test_package_relative_import_works(project: Project) -> None:
    project.plugin_pkg(
        "pkg", "from .helper import VALUE\nx = VALUE\n",
        helper="VALUE = 42\n",
    )
    source = scan([project.root / "plugins"]).sources[0]
    module = import_module(source)
    assert module.x == 42


def test_shadowed_package_keeps_its_plugin_dir_root(project: Project) -> None:
    """目录形态被覆盖时,落败者要记住**插件目录**而不是包目录。

    存 ``path.parent`` 的话,``root`` 是 ``.../low/audit``,不在 ``plugin_dirs``
    里 —— ``inspect`` 的优先级序号退成 -1,一个用户插件被显示成 ``<builtin>``。
    """
    low = project.plugin_dir("low")
    high = project.plugin_dir("high")
    project.write_in(low, "audit/__init__.py", "x = 1\n")
    project.write_in(high, "audit/__init__.py", "x = 2\n")

    result = scan([low, high])
    loser, winner = result.shadowed[0]
    assert loser.root == low
    assert loser.path == low / "audit" / "__init__.py"
    assert winner.root == high


def test_invalid_plugin_name_is_skipped_with_warning(project: Project) -> None:
    project.write("plugins/my-plugin.py", "x = 1\n")
    result = scan([project.root / "plugins"])
    assert result.sources == []
    assert len(result.warnings) == 1
    assert "my-plugin" in result.warnings[0]


def test_keyword_plugin_name_is_skipped(project: Project) -> None:
    project.write("plugins/class.py", "x = 1\n")
    result = scan([project.root / "plugins"])
    assert result.sources == []
    assert "class" in result.warnings[0]


def test_non_python_entries_are_not_name_checked(project: Project) -> None:
    """``data-2024.json`` 不是插件,不该被名字校验拦下 —— 它本来就不参与扫描。"""
    project.write("plugins/data-2024.json", "{}\n")
    project.plugin("real", "x = 1\n")
    result = scan([project.root / "plugins"])
    assert [s.plugin_id for s in result.sources] == ["real"]
    assert result.warnings == []
