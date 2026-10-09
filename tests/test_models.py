"""模型构造(设计文档 §9.3)。"""

from __future__ import annotations

import pytest

from agentscope.model import ChatModelBase

from agentscope_sidecar.errors import ConventionError
from agentscope_sidecar.models import BUILDERS, build_model, split_reference


# ------------------------------------------------------------------ 引用拆分


def test_split_reference() -> None:
    assert split_reference("dashscope/qwen-plus") == ("dashscope", "qwen-plus")


def test_split_keeps_slash_in_model() -> None:
    """模型名里可以带斜杠(有些 provider 这么写)。"""
    assert split_reference("openai/org/model") == ("openai", "org/model")


@pytest.mark.parametrize("ref", ["dashscope", "", "/model", "provider/"])
def test_bad_reference_raises(ref: str) -> None:
    with pytest.raises(ConventionError) as exc:
        split_reference(ref)
    assert "格式非法" in str(exc.value)


# ------------------------------------------------------------------ mock


def test_build_mock_model() -> None:
    model = build_model("mock/scripted", env={})
    assert isinstance(model, ChatModelBase)
    assert model.model_name if hasattr(model, "model_name") else True


def test_build_mock_uses_script_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOCK_LLM_SCRIPT", '[{"text": "来自环境变量"}]')
    model = build_model("mock/scripted", env={})
    assert model._script == [{"text": "来自环境变量"}]


# ------------------------------------------------------------------ 真实 provider


def test_build_dashscope_with_credential() -> None:
    model = build_model("dashscope/qwen-plus", env={"DASHSCOPE_API_KEY": "sk-x"})
    assert type(model).__name__ == "DashScopeChatModel"
    assert model.model == "qwen-plus"


def test_missing_credential_raises() -> None:
    with pytest.raises(ConventionError) as exc:
        build_model("dashscope/qwen-plus", env={})
    assert "DASHSCOPE_API_KEY" in str(exc.value)


def test_unknown_provider_raises() -> None:
    with pytest.raises(ConventionError) as exc:
        build_model("mycorp/whatever", env={})
    assert "未知的模型 provider" in str(exc.value)
    assert "换 agent.model" in str(exc.value)


def test_every_builder_produces_a_model() -> None:
    from agentscope_sidecar.convention import PROVIDER_ENV

    for provider in BUILDERS:
        var = PROVIDER_ENV[provider]
        model = build_model(f"{provider}/test-model", env={var: "sk-x"})
        assert isinstance(model, ChatModelBase), provider


def test_builder_set_matches_provider_env() -> None:
    from agentscope_sidecar.convention import PROVIDER_ENV

    assert set(BUILDERS) == set(PROVIDER_ENV)
