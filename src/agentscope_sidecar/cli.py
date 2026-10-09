"""命令行入口(设计文档 §11)。

    sidecar "任务"                        # once
    sidecar --mode json "任务"            # JSONL 事件流
    sidecar --mode rpc                    # JSON-RPC over stdio
    sidecar --mode http --port 8000       # HTTP 服务
    sidecar inspect                       # 加载报告
    sidecar check                         # 校验
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from .app import boot_sidecar
from .convention import PERMISSION_MODES, CliOptions, MODES
from .errors import SidecarError

COMMANDS = ("run", "inspect", "check", "list")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sidecar", description="运行时与插件分离的 Agent 底座",
    )
    # 第一个位置参数既可能是子命令,也可能是任务文本 —— 先收下来再判定,
    # 否则 `sidecar "任务"` 会被 argparse 当成非法子命令。
    parser.add_argument("first", nargs="?", default=None,
                        help="子命令(inspect/check/list)或任务文本")
    parser.add_argument("rest", nargs="*", help="子命令之后的位置参数")
    parser.add_argument("--mode", choices=list(MODES), default=None)
    parser.add_argument("--plugins", action="append", default=[], metavar="DIR")
    parser.add_argument("--model", default=None, metavar="provider/model")
    parser.add_argument("--system", default=None, help="内联系统提示词")
    parser.add_argument("--system-file", default=None, metavar="PATH")
    parser.add_argument("-e", "--extension", action="append", default=[],
                        metavar="PATH", help="临时加载单个插件文件")
    parser.add_argument("--skills", action="append", default=[],
                        metavar="PATH", help="技能目录(可重复,默认 ./skills)")
    parser.add_argument("--session-dir", default=None, metavar="PATH")
    parser.add_argument("--tools", default=None, metavar="A,B",
                        help="只保留这些工具")
    parser.add_argument("--middlewares", default=None, metavar="A,B",
                        help="只保留这些中间件(按类名)")
    parser.add_argument("--permission", choices=list(PERMISSION_MODES), default=None,
                        help="权限模式(非交互模式必读,见 §11.8)")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--reload", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--json", action="store_true", help="inspect 输出 JSON")
    return parser


def split_command(first: str | None, rest: list[str]) -> tuple[str, str | None]:
    """判定第一个位置参数是子命令还是任务文本。"""
    if first in COMMANDS:
        return first, (rest[0] if rest else None)
    return "run", first


def _to_options(args: argparse.Namespace, task: str | None) -> CliOptions:
    return CliOptions(
        task=task,
        mode=args.mode,
        plugins=tuple(args.plugins),
        model=args.model,
        system=args.system,
        system_file=args.system_file,
        extensions=tuple(args.extension),
        skills=tuple(args.skills),
        session_dir=args.session_dir,
        # 没给 --tools 是 None(不过滤);给了(哪怕空串)就是一次过滤
        tools=tuple(args.tools.split(",")) if args.tools is not None else None,
        middlewares=(
            tuple(args.middlewares.split(","))
            if args.middlewares is not None else None
        ),
        permission=args.permission,
        host=args.host,
        port=args.port,
        reload=args.reload,
        verbose=args.verbose,
        quiet=args.quiet,
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    command, task = split_command(args.first, args.rest)

    try:
        sidecar = boot_sidecar(_to_options(args, task))
    except SidecarError as exc:
        print(f"启动失败:{exc}", file=sys.stderr)
        return 1

    try:
        if command == "inspect":
            report = sidecar.report()
            if args.json:
                print(json.dumps(report, ensure_ascii=False, indent=2))
            else:
                _print_report(report)
            return 0

        if command == "list":
            for item in sidecar.report()["plugins"]:
                print(f"{item['state']:<9} {item['id']}")
            return 0

        if command == "check":
            failed = [
                p for p in sidecar.report()["plugins"] if p["state"] == "failed"
            ]
            for item in failed:
                print(f"FAILED {item['id']}: {item['error']}", file=sys.stderr)
            print(f"校验完成:{len(failed)} 个插件失败")
            return 1 if failed else 0

        try:
            return asyncio.run(sidecar.run())
        except SidecarError as exc:
            print(f"运行失败:{exc}", file=sys.stderr)
            # 「需要任务但没给」是用法错误,与启动/运行失败区分开
            return 2 if "需要一个任务参数" in str(exc) else 1
    finally:
        sidecar.shutdown()


def _print_report(report: dict) -> None:
    print(f"工作目录:{report['base_dir']}")
    print(f"模型:{report['model']}    模式:{report['mode']}    "
          f"权限:{report.get('permission', 'default')}")
    print("\n插件目录(优先级低 → 高):")
    for i, path in enumerate(report["plugin_dirs"]):
        print(f"  [{i}] {path}")

    print("\n插件:")
    for item in report["plugins"]:
        origin = "<builtin>" if item["order"] < 0 else f"[{item['order']}]"
        line = f"  [{item['state']:>8}] {item['id']:<18} {origin} {item['path']}"
        contributed = item.get("contributed") or {}
        if contributed:
            detail = " ".join(
                f"{k}={v}" for k, v in sorted(contributed.items())
            )
            line += f"  {detail}"
        if item.get("unresolved"):
            line += f"  未解析={item['unresolved']}"
        if item["shadowed_by"]:
            line += f"  被 {item['shadowed_by']} 覆盖"
        if item["error"]:
            line += f"  {item['error']}"
        print(line)

    if report["warnings"]:
        print("\n告警:")
        for warning in report["warnings"]:
            print(f"  {warning}")

    print(f"\n工具:{', '.join(report['tools']) or '(无)'}")
    print(f"中间件:{', '.join(report['middlewares']) or '(无)'}")
    if report.get("agent_overrides"):
        print("Agent 构造参数覆盖:")
        for field, owner in sorted(report["agent_overrides"].items()):
            print(f"  {field:<20} <- {owner}")
    if report.get("unresolved"):
        print(f"未解析的依赖:{', '.join(report['unresolved'])}")
    if report["routes"]:
        print("HTTP 路由:")
        for route, owner in sorted(report["routes"].items()):
            print(f"  {route} <- {owner}")
    if report.get("commands"):
        print("CLI 命令:")
        for name, owner in sorted(report["commands"].items()):
            print(f"  {name} <- {owner}")

    print("\n服务提供者:")
    for name, owner in sorted(report["services"].items()):
        print(f"  {name:<16} <- {owner}")

    counters = report["telemetry"]["counters"]
    if counters:
        print("\n事件计数:")
        for name, count in sorted(counters.items()):
            print(f"  {name:<28} {count}")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
