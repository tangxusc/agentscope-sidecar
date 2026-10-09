"""运行选项推导(设计文档 §9)。

**没有配置文件,也没有 agent 目录。** 所有相对路径以 CWD 为基准;插件目录
默认 ``./plugins``,用 ``--plugins`` / ``SIDECAR_PLUGINS`` 指定。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Mapping, Sequence

from .errors import ConventionError

logger = logging.getLogger("sidecar.convention")

Mode = Literal["once", "console", "json", "rpc", "http"]
MODES: tuple[str, ...] = ("once", "console", "json", "rpc", "http")

PermissionModeName = Literal[
    "default", "accept_edits", "explore", "bypass", "dont_ask",
]
PERMISSION_MODES: tuple[str, ...] = (
    "default", "accept_edits", "explore", "bypass", "dont_ask",
)

#: 只有 console 模式有用户可确认;其余模式下 ASK 无法满足(§11.8)。
INTERACTIVE_MODES = ("console",)

#: 模型推断用的 provider → 凭据环境变量映射(§9.3)。
PROVIDER_ENV: dict[str, str] = {
    "dashscope": "DASHSCOPE_API_KEY",
    "openai": "OPENAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "moonshot": "MOONSHOT_API_KEY",
}


@dataclass(frozen=True)
class CliOptions:
    """命令行解析结果。测试可直接构造它,绕过 argparse。"""

    task: str | None = None
    mode: str | None = None
    plugins: tuple[str, ...] = ()
    model: str | None = None
    system: str | None = None
    system_file: str | None = None
    extensions: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    session_dir: str | None = None
    tools: tuple[str, ...] | None = None
    middlewares: tuple[str, ...] | None = None
    permission: str | None = None
    host: str | None = None
    port: int | None = None
    reload: bool = False
    verbose: bool = False
    quiet: bool = False


@dataclass(frozen=True)
class RuntimeOptions:
    """``resolve()`` 的产物。所有路径已解析为绝对路径。"""

    # 路径
    base_dir: Path
    plugin_dirs: tuple[Path, ...]
    skills_dir: Path
    extra_skills: tuple[Path, ...]
    system_prompt_file: Path
    session_dir: Path

    # 装配
    model: str | None
    system_prompt: str | None
    tools: tuple[str, ...] | None
    middlewares: tuple[str, ...] | None
    permission: PermissionModeName

    # 运行
    mode: Mode
    host: str
    port: int
    task: str | None
    reload: bool
    log_level: str
    extensions: tuple[Path, ...] = field(default=())


def _env(name: str) -> str | None:
    value = os.environ.get(name)
    return value or None


def _split_env_paths(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [part for part in raw.split(os.pathsep) if part]


def _as_list(value: str | Sequence[str] | None) -> list[str]:
    """把多值选项归一成 ``list[str]``。路径与名字都用它。

    **必须挡掉「传了单个字符串」这种写法** —— ``list("./skills")`` 是
    ``['.', '/', 's', 'k', ...]``,每个字符当成一条路径。其中 ``"/"`` 恰好是
    存在的目录,于是 ``LocalSkillLoader("/", scan_subdir=True)`` 会去遍历整个
    文件系统。报错信息离根因十万八千里,不如在这里挡掉。
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    return [item for item in value if item]


def resolve(cli: CliOptions, *, cwd: Path | None = None) -> RuntimeOptions:
    """从命令行与环境变量推导运行选项。"""
    base = (cwd or Path.cwd()).resolve()

    explicit: list[str] = _as_list(cli.plugins) or _split_env_paths(_env("SIDECAR_PLUGINS"))
    plugin_dirs = [*_absolute(explicit, base), base / "plugins"]

    system_file = cli.system_file or "./SYSTEM.md"
    skills = _as_list(cli.skills) or ["./skills"]
    session_dir = cli.session_dir or _env("SIDECAR_SESSION_DIR") or "./var/sessions"

    mode = (cli.mode or _env("SIDECAR_MODE") or "").lower() or None
    if mode is None:
        mode = "once" if cli.task else "console"
    if mode not in MODES:
        raise ConventionError(f"未知运行模式 {mode!r};支持:{list(MODES)}")

    log_level = "INFO"
    if cli.verbose:
        log_level = "DEBUG"
    elif cli.quiet:
        log_level = "WARNING"
    else:
        log_level = (_env("SIDECAR_LOG_LEVEL") or "INFO").upper()

    permission = (cli.permission or _env("SIDECAR_PERMISSION") or "default").lower()
    if permission not in PERMISSION_MODES:
        raise ConventionError(
            f"未知权限模式 {permission!r};支持:{list(PERMISSION_MODES)}",
        )

    resolved_skills = _absolute(skills, base)
    return RuntimeOptions(
        base_dir=base,
        plugin_dirs=tuple(plugin_dirs),
        skills_dir=resolved_skills[0],
        extra_skills=tuple(resolved_skills[1:]),
        system_prompt_file=_absolute([system_file], base)[0],
        session_dir=_absolute([session_dir], base)[0],
        model=cli.model or _env("SIDECAR_MODEL"),
        system_prompt=cli.system,
        # None = 用户没说话(不过滤);空元组 = 明确要求一个都不留
        tools=None if cli.tools is None else tuple(_as_list(cli.tools)),
        middlewares=None if cli.middlewares is None else tuple(_as_list(cli.middlewares)),
        permission=permission,                      # type: ignore[arg-type]
        mode=mode,                                  # type: ignore[arg-type]
        host=cli.host or _env("SIDECAR_HOST") or "127.0.0.1",
        # ``port=0`` 是「让系统挑一个空闲端口」,是合法用法 —— 不能用 ``or`` 兜底,
        # 那会把它当成「没给」。只有 ``None`` 才是没给。
        port=cli.port if cli.port is not None else int(_env("SIDECAR_PORT") or 8000),
        task=cli.task,
        reload=cli.reload,
        log_level=log_level,
        extensions=tuple(_absolute(_as_list(cli.extensions), base)),
    )


def is_interactive(mode: str) -> bool:
    """只有 ``console`` 模式有用户可确认(§11.8)。"""
    return mode in INTERACTIVE_MODES


def _absolute(paths: Sequence[str], base: Path) -> list[Path]:
    out: list[Path] = []
    for raw in paths:
        p = Path(raw).expanduser()
        out.append(p if p.is_absolute() else (base / p).resolve())
    return out


#: 每个内置 provider 的默认模型。改这里等于改「不指定时用哪个模型」。
PROVIDER_DEFAULT_MODEL: dict[str, str] = {
    "dashscope": "qwen-plus",
    "openai": "gpt-4o",
    "deepseek": "deepseek-chat",
    "anthropic": "claude-sonnet-4-20250514",
    "moonshot": "moonshot-v1-8k",
    "mock": "scripted",
}

#: 免凭据 provider。没有任何凭据可用时兜底到它。
FALLBACK_PROVIDER = "mock"


def resolve_model_ref(
    options: RuntimeOptions,
    *,
    env: Mapping[str, str] | None = None,
) -> str:
    """确定要用的模型引用 ``provider/model``。

    显式给了 ``--model`` / ``SIDECAR_MODEL`` 就直接用;否则按「已设置的凭据
    环境变量」推断。多候选时报错而不是猜。

    **免凭据 provider**(``mock``)只在没有任何带凭据 provider 可用时兜底,
    并且会打出显式告警 —— 避免生产环境因为忘了设密钥而静默跑在离线模型上。
    """
    if options.model:
        return options.model

    env = env if env is not None else os.environ

    with_credential = [
        provider for provider, var in PROVIDER_ENV.items() if env.get(var)
    ]
    if len(with_credential) == 1:
        return _reference(with_credential[0])
    if len(with_credential) > 1:
        raise ConventionError(
            f"多个 provider 的凭据都已设置:{sorted(with_credential)};"
            f"请显式设置 --model 或 SIDECAR_MODEL 以消除歧义",
        )

    logger.warning(
        "没有检测到任何模型凭据,回退到免凭据 provider %r(%s)。"
        "如需真实模型,请设置 --model / SIDECAR_MODEL 或对应的凭据环境变量。",
        FALLBACK_PROVIDER, _reference(FALLBACK_PROVIDER),
    )
    return _reference(FALLBACK_PROVIDER)


def _reference(provider: str) -> str:
    default = PROVIDER_DEFAULT_MODEL.get(provider)
    if default is None:
        raise ConventionError(f"provider {provider!r} 没有默认模型")
    return f"{provider}/{default}"
