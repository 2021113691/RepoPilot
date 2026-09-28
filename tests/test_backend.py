import json

from repopilot.models.openai_compatible import BackendConfig, OpenAICompatibleBackend


def test_modelscope_env_alias(monkeypatch):
    monkeypatch.delenv("REPOPILOT_BASE_URL", raising=False)
    monkeypatch.delenv("REPOPILOT_MODEL", raising=False)
    monkeypatch.delenv("REPOPILOT_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://api-inference.modelscope.cn")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-secret")
    monkeypatch.setenv("MODEL_ID", "example/model")
    config = BackendConfig.from_env()
    assert config.base_url == "https://api-inference.modelscope.cn/v1"
    assert config.model == "example/model"


def test_backend_parses_tool_response_without_logging_key(monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return json.dumps({
                "model": "example/model",
                "choices": [{"message": {"content": None, "tool_calls": [{"id": "call-1", "function": {"name": "list_files", "arguments": "{}"}}]}, "finish_reason": "tool_calls"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 4},
            }).encode()

    def fake_urlopen(request, timeout):
        assert request.full_url == "https://example.com/v1/chat/completions"
        assert json.loads(request.data)["tools"]
        return FakeResponse()

    monkeypatch.setattr("repopilot.models.openai_compatible.urlopen", fake_urlopen)
    backend = OpenAICompatibleBackend(BackendConfig("https://example.com/v1", "test-secret", "example/model"))
    response = backend.chat([{"role": "user", "content": "hello"}], [{"type": "function", "function": {"name": "list_files"}}])
    assert response.tool_calls[0].name == "list_files"
    assert response.token_usage.input_tokens == 10
    assert response.token_usage.output_tokens == 4
