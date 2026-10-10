"""The live CLI is tested entirely offline; no real credentials are loaded."""
import importlib.util
import json
from pathlib import Path
import sys

import httpx
import pytest

from just_read.settings import Settings


@pytest.fixture
def cli(monkeypatch):
    path = Path(__file__).resolve().parents[2] / "scripts" / "test-live-api.py"
    spec = importlib.util.spec_from_file_location("justread_live_cli", path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("mode", [[], ["--check-config"], ["--model-only"], ["--search-only"], ["--full"]])
def test_missing_credentials_refuse_without_network_or_database(cli, monkeypatch, capsys, tmp_path, mode):
    monkeypatch.setattr(cli.Settings, "from_env", lambda: Settings())
    monkeypatch.setattr(cli, "ROOT", tmp_path)

    def unexpected(*_args, **_kwargs):
        pytest.fail("missing configuration must not open a client")

    monkeypatch.setattr(cli.httpx, "AsyncClient", unexpected)
    assert cli.main(mode) == 2
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert records[-1]["code"] == "PROVIDER_CONFIG"
    assert not (tmp_path / ".test-data").exists()


@pytest.mark.asyncio
async def test_probe_injected_mock_transport_needs_no_search_key_and_redacts_failure(cli, capsys, monkeypatch):
    settings = Settings(llm_provider="openai", llm_model="server-test-model", llm_api_key="do-not-print-this")
    calls = []

    def handler(request):
        payload = json.loads(request.content)
        calls.append(payload)
        assert payload["stream"] is False and payload["max_output_tokens"] == 1024
        return httpx.Response(200, json={"status": "completed", "output": [{"type": "message", "content": [
            {"type": "output_text", "text": '{"result":"ok"}'}]}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await cli.model_probe(settings, client) == {"result": "ok"}
    assert len(calls) == 1
    assert "do-not-print-this" not in capsys.readouterr().out

    # Unexpected exception messages and arbitrary upstream codes must not escape.
    monkeypatch.setattr(cli.Settings, "from_env", lambda: settings)

    async def secret_failure(_):
        raise cli.PipelineError("do-not-print-this", "secret provider body")

    monkeypatch.setattr(cli, "model_probe", secret_failure)
    # main owns its own event loop, so call it outside this running async loop.
    import asyncio
    assert await asyncio.to_thread(cli.main, ["--model-only"]) == 1
    output = capsys.readouterr().out
    assert "do-not-print-this" not in output and "secret provider body" not in output
    assert json.loads(output)["code"] == "GENERATION_FAILED"


def test_config_status_does_not_echo_misplaced_secret_or_endpoint(cli, monkeypatch, capsys):
    settings = Settings(llm_model="misplaced-model-secret", llm_api_key="private-model-key",
                        search_api_key="private-search-key", llm_base_url="https://private.invalid/secret")
    monkeypatch.setattr(cli.Settings, "from_env", lambda: settings)
    assert cli.main(["--check-config"]) == 0
    output = capsys.readouterr().out
    assert all(secret not in output for secret in [settings.llm_model, settings.llm_api_key,
                                                   settings.search_api_key, settings.llm_base_url])
    assert json.loads(output) == {"event": "config", "provider": "openai", "model_configured": True,
                                  "llm_key_configured": True, "search_key_configured": True, "ready": True,
                                  "search_provider": "tavily", "search_ready": True}


def test_bad_cli_arguments_do_not_echo_accidentally_pasted_key(cli, capsys):
    assert cli.main(["--api-key", "accidental-secret"]) == 2
    captured = capsys.readouterr()
    assert "accidental-secret" not in captured.out + captured.err
    assert json.loads(captured.out)["code"] == "INVALID_ARGUMENTS"


def test_openrouter_config_check_and_full_accept_one_key_auto_search(cli, monkeypatch, capsys):
    settings = Settings(llm_provider="openrouter", llm_model="vendor/test-model", llm_api_key="private-key")
    monkeypatch.setattr(cli.Settings, "from_env", lambda: settings)
    monkeypatch.setattr(cli.httpx, "AsyncClient", lambda *a, **k: pytest.fail("config check must not call network"))
    assert cli.main(["--check-config"]) == 0
    record = json.loads(capsys.readouterr().out)
    assert record["provider"] == "openrouter" and record["llm_key_configured"] is True
    assert record["search_key_configured"] is False and record["ready"] is True
    assert record["search_provider"] == "openrouter" and record["search_ready"] is True
    cli.check_settings(settings, "full")


def test_live_cli_full_allows_missing_search_key_but_search_probe_requires_it(cli, monkeypatch, capsys):
    settings = Settings(llm_provider="openrouter", llm_model="vendor/test-model", llm_api_key="private-key",
                        search_provider="tavily")
    monkeypatch.setattr(cli.Settings, "from_env", lambda: settings)
    monkeypatch.setattr(cli.httpx, "AsyncClient", lambda *a, **k: pytest.fail("missing search config must not call network"))
    cli.check_settings(settings, "full")
    assert cli.main(["--search-only"]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "PROVIDER_NOT_CONFIGURED"


@pytest.mark.asyncio
async def test_openrouter_model_probe_uses_chat_schema_without_search(cli):
    settings = Settings(llm_provider="openrouter", llm_model="vendor/test-model", llm_api_key="private-key")

    def handler(request):
        payload = json.loads(request.content)
        assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
        assert payload["stream"] is False and payload["max_tokens"] == 1024
        assert payload["response_format"]["type"] == "json_schema"
        assert payload["provider"]["require_parameters"] is False
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "role": "assistant", "content": '{"result":"ok"}'}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await cli.model_probe(settings, client) == {"result": "ok"}


@pytest.mark.asyncio
async def test_openrouter_search_probe_only_needs_model_key_and_returns_safe_counts(cli, monkeypatch, capsys):
    settings = Settings(llm_provider="openrouter", llm_model="vendor/test-model", llm_api_key="private-key")
    excerpt = "TaskGroup cancels remaining tasks when a task fails with an exception other than CancelledError."
    calls = []

    def handler(request):
        payload = json.loads(request.content)
        calls.append(payload)
        assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer private-key"
        assert payload["stream"] is False and payload["max_tokens"] == 1024
        assert payload["tools"][0]["type"] == "openrouter:web_search"
        assert "response_format" not in payload and "plugins" not in payload
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "role": "assistant", "content": "Generated response must not be reported as source evidence.",
            "annotations": [{"type": "url_citation", "url_citation": {
                "url": "https://docs.python.org/3/library/asyncio-task.html", "title": "Coroutines and Tasks",
                "content": excerpt}}]}}]})

    expected = {"search_provider": "openrouter", "sources": 1, "excerpt_characters": len(excerpt)}
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await cli.search_probe(settings, client) == expected
    assert len(calls) == 1

    # Exercise the CLI mode without opening a second, uninjected network client.
    async def already_probed(value):
        assert value is settings
        return expected

    monkeypatch.setattr(cli.Settings, "from_env", lambda: settings)
    monkeypatch.setattr(cli, "search_probe", already_probed)
    import asyncio
    assert await asyncio.to_thread(cli.main, ["--search-only"]) == 0
    output = capsys.readouterr().out
    assert json.loads(output) == {"event": "search_ok", **expected}
    assert "private-key" not in output and excerpt not in output


@pytest.mark.asyncio
@pytest.mark.parametrize("annotations", [
    None,
    [],
    [{"type": "url_citation", "url_citation": {
        "url": "https://docs.python.org/3/library/asyncio-task.html", "content": "Too short."}}],
    [{"type": "url_citation", "url_citation": {
        "url": "http://127.0.0.1/private", "content": "Unusable private source. " * 10}}],
])
async def test_search_probe_rejects_generated_answer_without_usable_excerpts(cli, annotations):
    settings = Settings(llm_provider="openrouter", llm_model="vendor/test-model", llm_api_key="private-key")
    calls = []

    def handler(request):
        calls.append(request)
        message = {"role": "assistant", "content": (
            "Official documentation https://docs.python.org/3/library/asyncio-task.html says "
            "TaskGroup cancels sibling tasks on failure. " * 5)}
        if annotations is not None:
            message["annotations"] = annotations
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": message}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(cli.PipelineError) as caught:
            await cli.search_probe(settings, client)
    assert caught.value.code == "INSUFFICIENT_EVIDENCE"
    assert len(calls) == 1


@pytest.mark.parametrize("settings", [Settings(llm_provider="fixture", test_mode=True),
    Settings(llm_model="model", llm_api_key="private-model-key", search_api_key="private-search-key", test_mode=True)])
def test_live_cli_refuses_test_modes(cli, monkeypatch, capsys, settings):
    monkeypatch.setattr(cli.Settings, "from_env", lambda: settings)
    assert cli.main(["--model-only"]) == 2
    output = capsys.readouterr().out
    assert json.loads(output)["code"] == "LIVE_MODE_REQUIRED"
    assert "private-model-key" not in output and "private-search-key" not in output
