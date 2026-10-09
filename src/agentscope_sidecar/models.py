"""默认模型构造(设计文档 §9.3)。

底座在建 Agent 前造一个默认 model 实例。插件要换模型就在 ``setup`` 里
``agent.model = ...`` —— 底座不提供 provider 注册表。

**凭据由这里从环境变量取。** AgentScope 的凭据类不读环境变量
(``DashScopeCredential`` 的 ``api_key`` 是必填字段,类里没有 ``getenv``),
所以必须显式传。
"""

from __future__ import annotations

import os
from typing import Any, Callable, Mapping

from agentscope.credential import (
    AnthropicCredential,
    DashScopeCredential,
    DeepSeekCredential,
    MoonshotCredential,
    OpenAICredential,
)
from agentscope.model import (
    AnthropicChatModel,
    ChatModelBase,
    DashScopeChatModel,
    DeepSeekChatModel,
    MoonshotChatModel,
    OpenAIChatModel,
)

from .convention import FALLBACK_PROVIDER, PROVIDER_ENV
from .errors import ConventionError


def _build(credential_cls: type, model_cls: type) -> Callable[[str, str], Any]:
    def build(model: str, api_key: str) -> Any:
        return model_cls(credential=credential_cls(api_key=api_key), model=model)

    return build


#: provider -> 构造函数 ``(model, api_key) -> ChatModelBase``。
BUILDERS: dict[str, Callable[[str, str], Any]] = {
    "dashscope": _build(DashScopeCredential, DashScopeChatModel),
    "openai": _build(OpenAICredential, OpenAIChatModel),
    "deepseek": _build(DeepSeekCredential, DeepSeekChatModel),
    "anthropic": _build(AnthropicCredential, AnthropicChatModel),
    "moonshot": _build(MoonshotCredential, MoonshotChatModel),
}


def split_reference(reference: str) -> tuple[str, str]:
    """把 ``provider/model`` 拆开。格式非法时抛 ``ConventionError``。"""
    if "/" not in reference:
        raise ConventionError(
            f"模型引用 {reference!r} 格式非法,期望 'provider/model'",
        )
    provider, model = reference.split("/", 1)
    if not provider or not model:
        raise ConventionError(
            f"模型引用 {reference!r} 格式非法,期望 'provider/model'",
        )
    return provider, model


def build_model(
    reference: str,
    *,
    env: Mapping[str, str] | None = None,
) -> ChatModelBase:
    """按 ``provider/model`` 造一个模型实例。"""
    provider, model = split_reference(reference)

    if provider == FALLBACK_PROVIDER:
        return _build_mock(model)

    builder = BUILDERS.get(provider)
    if builder is None:
        known = sorted([*BUILDERS, FALLBACK_PROVIDER])
        raise ConventionError(
            f"未知的模型 provider {provider!r};内置支持:{known}。"
            f"要接自己的 provider,写个插件在 setup 里换 agent.model",
        )

    var = PROVIDER_ENV[provider]
    api_key = (env if env is not None else os.environ).get(var)
    if not api_key:
        raise ConventionError(f"provider {provider!r} 需要环境变量 {var}")

    return builder(model, api_key)


def _build_mock(model: str) -> ChatModelBase:
    """内置离线模型。延迟导入 —— 它只在没凭据时才用到。"""
    from .mock_model import ScriptedChatModel, load_script

    script, fallback = load_script()
    return ScriptedChatModel(model=model, script=script, fallback_text=fallback)


__all__ = ["BUILDERS", "build_model", "split_reference"]
