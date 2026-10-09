"""让被测试的子进程也上报覆盖率。

测试里有大量 `subprocess` 调用(CLI 子进程、RPC stdio、HTTP 服务)。父进程的
coverage 看不到它们 —— 没有这个钩子,`cli.py` 与 `modes/*` 会一直显示 0%。

用法:子进程的 `PYTHONPATH` 里加上这个目录,并设置 `COVERAGE_PROCESS_START`
指向配置文件。Python 启动时会自动 import `sitecustomize`,于是子进程也开测。

**只在父进程已经在测量时才生效** —— 否则普通 `pytest -q` 也会到处生成
`.coverage.*` 数据文件。
"""

from __future__ import annotations

import os

if os.environ.get("COVERAGE_PROCESS_START"):
    try:
        import coverage

        coverage.process_startup()
    except Exception:  # pragma: no cover - 测量失败不该影响被测进程
        pass
