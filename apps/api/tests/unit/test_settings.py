import pytest
from pydantic import ValidationError

from content_agents.config import OllamaSettings


def test_defaults_use_one_local_model_for_both_tiers(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "OLLAMA_HOST",
        "OLLAMA_BULK_MODEL",
        "OLLAMA_WRITING_MODEL",
        "OLLAMA_NUM_CTX",
        "OLLAMA_THINK",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = OllamaSettings(_env_file=None)

    assert settings.host == "http://localhost:11434"
    assert settings.model_for("bulk") == "qwen3-vl:8b-instruct"
    assert settings.model_for("writing") == "qwen3-vl:8b-instruct"
    assert settings.num_ctx == 32_768
    assert settings.think is False


def test_env_vars_override_models(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_BULK_MODEL", "llama3.2:1b")
    monkeypatch.setenv("OLLAMA_WRITING_MODEL", "qwen3-vl:8b")

    settings = OllamaSettings(_env_file=None)

    assert settings.model_for("bulk") == "llama3.2:1b"
    assert settings.model_for("writing") == "qwen3-vl:8b"


def test_num_ctx_must_be_positive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_NUM_CTX", "0")
    with pytest.raises(ValidationError):
        OllamaSettings(_env_file=None)
