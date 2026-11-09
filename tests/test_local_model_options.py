"""The local llama-server model must not think silently before answering."""
from app import utilities
from app.config import Config


def test_thinking_is_off_for_the_local_model(monkeypatch):
    monkeypatch.setattr(Config, "OLLAMA_THINKING", False, raising=False)
    payload = utilities._apply_local_model_options("ollama", {"model": "qwen3.6-27b"})
    assert payload["chat_template_kwargs"] == {"enable_thinking": False}


def test_thinking_can_be_switched_back_on(monkeypatch):
    monkeypatch.setattr(Config, "OLLAMA_THINKING", True, raising=False)
    payload = utilities._apply_local_model_options("ollama", {"model": "qwen3.6-27b"})
    assert "chat_template_kwargs" not in payload


def test_other_providers_are_left_alone(monkeypatch):
    monkeypatch.setattr(Config, "OLLAMA_THINKING", False, raising=False)
    payload = utilities._apply_local_model_options("groq", {"model": "x"})
    assert payload == {"model": "x"}
