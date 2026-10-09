"""运行选项推导(设计文档 §9)。"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentscope_sidecar.convention import (
    FALLBACK_PROVIDER,
    PERMISSION_MODES,
    PROVIDER_DEFAULT_MODEL,
    PROVIDER_ENV,
    CliOptions,
    is_interactive,
    resolve,
    resolve_model_ref,
)
from agentscope_sidecar.errors import ConventionError

CRED_VARS = tuple(PROVIDER_ENV.values())


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (*CRED_VARS, "SIDECAR_MODEL", "SIDECAR_PLUGINS", "SIDECAR_MODE",
                "SIDECAR_HOST", "SIDECAR_PORT", "SIDECAR_LOG_LEVEL",
                "SIDECAR_PERMISSION", "SIDECAR_SESSION_DIR"):
        monkeypatch.delenv(var, raising=False)


# ------------------------------------------------------------------ 路径


def test_paths_are_absolute_relative_to_cwd(tmp_path: Path) -> None:
    options = resolve(CliOptions(), cwd=tmp_path)
    assert options.base_dir == tmp_path
    assert options.plugin_dirs == (tmp_path / "plugins",)
    assert options.skills_dir == tmp_path / "skills"
    assert options.system_prompt_file == tmp_path / "SYSTEM.md"
    assert options.session_dir == tmp_path / "var" / "sessions"


def test_tilde_is_expanded(tmp_path: Path) -> None:
    options = resolve(CliOptions(system_file="~/S.md"), cwd=tmp_path)
    assert options.system_prompt_file == Path.home() / "S.md"


def test_absolute_path_kept(tmp_path: Path) -> None:
    other = tmp_path / "elsewhere" / "S.md"
    options = resolve(CliOptions(system_file=str(other)), cwd=tmp_path)
    assert options.system_prompt_file == other


def test_plugins_env_and_flag_do_not_stack(tmp_path: Path, monkeypatch) -> None:
    """给了 ``--plugins`` 就忽略环境变量 —— 实现是 ``or``,不是叠加。"""
    monkeypatch.setenv("SIDECAR_PLUGINS", "/env/dir")
    from_env = resolve(CliOptions(), cwd=tmp_path)
    assert from_env.plugin_dirs == (Path("/env/dir"), tmp_path / "plugins")

    both = resolve(CliOptions(plugins=("/cli/dir",)), cwd=tmp_path)
    assert both.plugin_dirs == (Path("/cli/dir"), tmp_path / "plugins")


def test_plugins_env_is_path_separated(tmp_path: Path, monkeypatch) -> None:
    import os

    monkeypatch.setenv("SIDECAR_PLUGINS", os.pathsep.join(["/a", "/b"]))
    options = resolve(CliOptions(), cwd=tmp_path)
    assert options.plugin_dirs == (Path("/a"), Path("/b"), tmp_path / "plugins")


def test_skills_is_repeatable(tmp_path: Path) -> None:
    options = resolve(CliOptions(skills=("/one", "/two")), cwd=tmp_path)
    assert options.skills_dir == Path("/one")
    assert options.extra_skills == (Path("/two"),)


def test_skills_default(tmp_path: Path) -> None:
    options = resolve(CliOptions(), cwd=tmp_path)
    assert options.skills_dir == tmp_path / "skills"
    assert options.extra_skills == ()


# ------------------------------------------------------------------ 模式


def test_mode_defaults_to_once_with_task(tmp_path: Path) -> None:
    assert resolve(CliOptions(task="hi"), cwd=tmp_path).mode == "once"


def test_mode_defaults_to_console_without_task(tmp_path: Path) -> None:
    assert resolve(CliOptions(), cwd=tmp_path).mode == "console"


def test_explicit_mode_wins(tmp_path: Path) -> None:
    assert resolve(CliOptions(mode="http", task="x"), cwd=tmp_path).mode == "http"


def test_mode_from_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SIDECAR_MODE", "RPC")
    assert resolve(CliOptions(), cwd=tmp_path).mode == "rpc"


def test_unknown_mode_raises(tmp_path: Path) -> None:
    with pytest.raises(ConventionError) as exc:
        resolve(CliOptions(mode="nope"), cwd=tmp_path)
    assert "未知运行模式" in str(exc.value)


def test_is_interactive_only_console() -> None:
    assert is_interactive("console") is True
    assert all(is_interactive(m) is False for m in ("once", "json", "rpc", "http"))


# ------------------------------------------------------------------ 日志与权限


def test_verbose_and_quiet(tmp_path: Path) -> None:
    assert resolve(CliOptions(verbose=True), cwd=tmp_path).log_level == "DEBUG"
    assert resolve(CliOptions(quiet=True), cwd=tmp_path).log_level == "WARNING"
    assert resolve(CliOptions(), cwd=tmp_path).log_level == "INFO"


def test_verbose_wins_over_quiet(tmp_path: Path) -> None:
    options = resolve(CliOptions(verbose=True, quiet=True), cwd=tmp_path)
    assert options.log_level == "DEBUG"


def test_log_level_from_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SIDECAR_LOG_LEVEL", "error")
    assert resolve(CliOptions(), cwd=tmp_path).log_level == "ERROR"


def test_permission_default(tmp_path: Path) -> None:
    assert resolve(CliOptions(), cwd=tmp_path).permission == "default"


def test_permission_from_flag_and_env(tmp_path: Path, monkeypatch) -> None:
    assert resolve(CliOptions(permission="bypass"), cwd=tmp_path).permission == "bypass"
    monkeypatch.setenv("SIDECAR_PERMISSION", "EXPLORE")
    assert resolve(CliOptions(), cwd=tmp_path).permission == "explore"


def test_unknown_permission_raises(tmp_path: Path) -> None:
    with pytest.raises(ConventionError) as exc:
        resolve(CliOptions(permission="yolo"), cwd=tmp_path)
    assert "未知权限模式" in str(exc.value)


def test_all_permission_modes_accepted(tmp_path: Path) -> None:
    for mode in PERMISSION_MODES:
        assert resolve(CliOptions(permission=mode), cwd=tmp_path).permission == mode


# ------------------------------------------------------------------ 主机端口


def test_host_port_defaults(tmp_path: Path) -> None:
    options = resolve(CliOptions(), cwd=tmp_path)
    assert options.host == "127.0.0.1"
    assert options.port == 8000


def test_host_port_from_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SIDECAR_HOST", "0.0.0.0")
    monkeypatch.setenv("SIDECAR_PORT", "9001")
    options = resolve(CliOptions(), cwd=tmp_path)
    assert options.host == "0.0.0.0"
    assert options.port == 9001


def test_flag_wins_over_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SIDECAR_PORT", "9001")
    assert resolve(CliOptions(port=1234), cwd=tmp_path).port == 1234


# ------------------------------------------------------------------ 模型推断


def test_explicit_model_wins() -> None:
    options = resolve(CliOptions(model="dashscope/qwen-max"), cwd=Path.cwd())
    assert resolve_model_ref(options, env={}) == "dashscope/qwen-max"


def test_model_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIDECAR_MODEL", "openai/gpt-4o-mini")
    options = resolve(CliOptions(), cwd=Path.cwd())
    assert resolve_model_ref(options, env={}) == "openai/gpt-4o-mini"


def test_single_credential_inferred() -> None:
    options = resolve(CliOptions(), cwd=Path.cwd())
    ref = resolve_model_ref(options, env={"DASHSCOPE_API_KEY": "sk-x"})
    assert ref == f"dashscope/{PROVIDER_DEFAULT_MODEL['dashscope']}"


def test_multiple_credentials_raise() -> None:
    options = resolve(CliOptions(), cwd=Path.cwd())
    with pytest.raises(ConventionError) as exc:
        resolve_model_ref(
            options, env={"DASHSCOPE_API_KEY": "a", "OPENAI_API_KEY": "b"},
        )
    assert "多个 provider 的凭据都已设置" in str(exc.value)


def test_no_credential_falls_back_to_mock() -> None:
    options = resolve(CliOptions(), cwd=Path.cwd())
    ref = resolve_model_ref(options, env={})
    assert ref == f"{FALLBACK_PROVIDER}/{PROVIDER_DEFAULT_MODEL[FALLBACK_PROVIDER]}"


def test_empty_credential_is_not_a_credential() -> None:
    """空字符串不算设了凭据 —— 否则 ``VAR=`` 会静默改变推断结果。"""
    options = resolve(CliOptions(), cwd=Path.cwd())
    ref = resolve_model_ref(options, env={"DASHSCOPE_API_KEY": ""})
    assert ref.startswith(FALLBACK_PROVIDER)


def test_every_provider_has_default_model() -> None:
    for provider in (*PROVIDER_ENV, FALLBACK_PROVIDER):
        assert provider in PROVIDER_DEFAULT_MODEL
