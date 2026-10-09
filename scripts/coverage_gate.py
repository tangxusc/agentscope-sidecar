#!/usr/bin/env python3
"""检查覆盖率是否满足行/分支双阈值。

coverage 自带的 ``fail_under`` 只看合并值 —— 行覆盖率 95% 而分支只有 60% 时
它照样通过。这里把两个数分开卡。

用法::

    COVERAGE_PROCESS_START=$PWD/pyproject.toml \\
      pytest -q --cov=agentscope_sidecar --cov-branch
    python scripts/coverage_gate.py

退出码:0 达标;1 有文件或总量不达标;2 没有覆盖率数据。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

LINE_MIN = 80.0
BRANCH_MIN = 80.0
#: 语句太少的文件不参与单文件判定 —— 分母小,一行的差异就是十几个百分点。
MIN_STATEMENTS = 20


def load_totals() -> dict:
    # 不叫 .coverage.json —— 那个名字会被 coverage 当成数据文件去读
    report = Path(".coverage-report.json")
    subprocess.run(
        [sys.executable, "-m", "coverage", "json", "-o", str(report), "-q"],
        check=True,
    )
    if not report.exists():
        print("没有覆盖率数据:先跑一次带 --cov 的 pytest", file=sys.stderr)
        raise SystemExit(2)
    return json.loads(report.read_text(encoding="utf-8"))


def percent(covered: int, total: int) -> float:
    return 100.0 * covered / total if total else 100.0


def main() -> int:
    data = load_totals()
    totals = data["totals"]

    line = percent(totals["covered_lines"], totals["num_statements"])
    branch = percent(totals["covered_branches"], totals["num_branches"])

    print(f"行覆盖率  : {line:5.1f}%  (要求 ≥ {LINE_MIN:.0f}%)")
    print(f"分支覆盖率: {branch:5.1f}%  (要求 ≥ {BRANCH_MIN:.0f}%)")

    failures: list[str] = []
    if line < LINE_MIN:
        failures.append(f"总行覆盖率 {line:.1f}% < {LINE_MIN:.0f}%")
    if branch < BRANCH_MIN:
        failures.append(f"总分支覆盖率 {branch:.1f}% < {BRANCH_MIN:.0f}%")

    weak: list[tuple[str, float, float]] = []
    for path, entry in data["files"].items():
        summary = entry["summary"]
        if summary["num_statements"] < MIN_STATEMENTS:
            continue
        name = path.split("agentscope_sidecar/")[-1]
        file_line = percent(summary["covered_lines"], summary["num_statements"])
        file_branch = percent(summary["covered_branches"], summary["num_branches"])
        if file_line < LINE_MIN or file_branch < BRANCH_MIN:
            weak.append((name, file_line, file_branch))

    if weak:
        print("\n以下文件未达标:")
        for name, file_line, file_branch in sorted(weak):
            print(f"  {name:32} 行 {file_line:5.1f}%  分支 {file_branch:5.1f}%")
        failures.append(f"{len(weak)} 个文件未达标")

    if failures:
        print("\n未通过:")
        for item in failures:
            print(f"  - {item}")
        return 1

    print("\n通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
