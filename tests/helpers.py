"""E2E 测试夹具:构造临时项目、在进程内/子进程里跑底座。"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any, Sequence

import pytest

from agentscope_sidecar import CliOptions, Sidecar, boot_sidecar

#: 清掉真实凭据,让模型推断稳定落在内置 mock provider 上。
_CLEAR_ENV = (
    "DASHSCOPE_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY",
    "ANTHROPIC_API_KEY", "MOONSHOT_API_KEY",
    "SIDECAR_MODEL", "SIDECAR_PLUGINS", "SIDECAR_MODE",
    "SIDECAR_HOST", "SIDECAR_PORT", "SIDECAR_LOG_LEVEL",
    "SIDECAR_SESSION_DIR", "MOCK_LLM_SCRIPT",
)

#: 不该拿来当插件名的通用词。**插件文件名就是插件 id**,会出现在 ``list`` /
#: ``inspect`` 报告里 —— ``policy.py`` 这类放到哪都成立的名字让人分不出谁是谁。
#: ``examples/`` 与 ``tests/e2e/agent/`` 都靠它守住。
GENERIC_PLUGIN_NAMES = frozenset({
    "policy", "plugin", "plugins", "main", "config", "settings",
    "utils", "helpers", "common", "base",
})


class Project:
    """一个临时项目目录。"""

    def __init__(self, root: Path) -> None:
        self.root = root
        (root / "plugins").mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------ 写文件

    def write(self, relpath: str, content: str) -> Path:
        path = self.root / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(content).lstrip("\n"), encoding="utf-8")
        return path

    def plugin(self, name: str, code: str) -> Path:
        """单文件形态插件。"""
        return self.write(f"plugins/{name}.py", code)

    def plugin_pkg(self, name: str, init: str, **files: str) -> Path:
        """目录形态插件。``__`` 在文件名里表示路径分隔(``data__x.json``)。

        没有后缀的键名默认补 ``.py``。
        """
        self.write(f"plugins/{name}/__init__.py", init)
        for filename, content in files.items():
            rel = filename.replace("__", "/")
            if "." not in rel.rsplit("/", 1)[-1]:
                rel += ".py"
            self.write(f"plugins/{name}/{rel}", content)
        return self.root / "plugins" / name

    def system(self, text: str) -> Path:
        return self.write("SYSTEM.md", text)

    def skill(self, name: str, description: str, body: str) -> Path:
        return self.write(
            f"skills/{name}/SKILL.md",
            f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n",
        )

    def plugin_dir(self, name: str) -> Path:
        """另一个插件目录(用于多目录场景)。"""
        path = self.root / name
        path.mkdir(parents=True, exist_ok=True)
        return path

    def write_in(self, directory: Path, relpath: str, content: str) -> Path:
        path = directory / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(content).lstrip("\n"), encoding="utf-8")
        return path

    # ------------------------------------------------------------ 启动

    def env(self, **overrides: str) -> dict[str, str]:
        env = dict(os.environ)
        for key in _CLEAR_ENV:
            env.pop(key, None)
        _enable_subprocess_coverage(env)
        env.update(overrides)
        return env

    def boot(self, *, cwd: Path | None = None, **kwargs: Any) -> Sidecar:
        return boot_sidecar(
            CliOptions(**kwargs), cwd=cwd or self.root,
        )

    async def aboot(self, *, cwd: Path | None = None, **kwargs: Any) -> Sidecar:
        """异步启动 —— 供已在事件循环里的测试使用。"""
        from agentscope_sidecar.convention import resolve

        options = resolve(CliOptions(**kwargs), cwd=cwd or self.root)
        sidecar = Sidecar(options)
        await sidecar.aboot()
        return sidecar

    def cli(
        self, *args: str, env: dict[str, str] | None = None, timeout: int = 60,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "agentscope_sidecar", *args],
            cwd=self.root,
            env=env or self.env(),
            capture_output=True,
            text=True,
            timeout=timeout,
        )


@pytest.fixture
def project(tmp_path: Path) -> Project:
    return Project(tmp_path / "proj")


def _enable_subprocess_coverage(env: dict[str, str]) -> None:
    """父进程在测覆盖率时,让子进程也上报。

    大量测试跑的是真实子进程(CLI / RPC / HTTP)。不打通这一层,``cli.py`` 与
    ``modes/*`` 会一直显示 0% —— 不是因为没测,而是因为没测到。

    只在 ``COVERAGE_PROCESS_START`` 已存在时生效,普通 ``pytest -q`` 不受影响。
    """
    config = os.environ.get("COVERAGE_PROCESS_START")
    if not config:
        return

    env["COVERAGE_PROCESS_START"] = config
    env["COVERAGE_FILE"] = str(Path(config).parent / ".coverage")

    boot = str(Path(__file__).parent / "_coverage_boot")
    parts = [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p]
    if boot not in parts:
        parts.insert(0, boot)
    env["PYTHONPATH"] = os.pathsep.join(parts)


def load_script(steps: Sequence[dict[str, Any]]) -> str:
    """把脚本转成 MOCK_LLM_SCRIPT 环境变量值。"""
    import json

    return json.dumps(list(steps), ensure_ascii=False)


def copy_tree(src: Path, dest: Path) -> Path:
    """递归复制目录,**逐文件读写**而不是 ``shutil.copytree``。

    ``copytree`` 会走沙箱的「host recursive copy」通道,那条通道偶尔被文件策略
    拒绝(``Brokered host recursive copy source refused by file policy``),
    一次拒绝就让整条用例报 ERROR。逐文件 ``read_text`` / ``write_text`` 走的是
    普通 open,稳定得多,而且复制了什么一目了然。
    """
    dest.mkdir(parents=True, exist_ok=True)
    for path in src.rglob("*"):
        relative = path.relative_to(src)
        if path.is_dir():
            (dest / relative).mkdir(parents=True, exist_ok=True)
            continue
        target = dest / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.read_bytes())
    return dest
