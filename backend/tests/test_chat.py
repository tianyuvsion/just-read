import json
import pytest
from fastapi.testclient import TestClient

from just_read.app import create_app
from just_read.settings import Settings


@pytest.fixture
def fixture_client(tmp_path):
    settings = Settings(
        test_mode=True,
        llm_provider="fixture",
        llm_model="moonshotai/kimi-k3",
        fixture_delay_seconds=0.001,
        database_path=str(tmp_path / 'chat.sqlite3'),
    )
    app = create_app(settings)
    with TestClient(app) as client:
        # Establish session cookie first
        client.get("/api/v1/health")
        yield client


def test_chat_stream_fixture(fixture_client):
    response = fixture_client.post(
        "/api/v1/chat/stream",
        json={
            "messages": [
                {"role": "user", "content": "你好，请介绍一下你自己"}
            ]
        },
    )
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]

    text = response.text
    assert "data: " in text
    assert "data: [DONE]" in text

    # Check that chunks can be parsed
    tokens = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("data: ") and line != "data: [DONE]":
            chunk = json.loads(line[6:])
            tokens.append(chunk.get("content", ""))

    full_text = "".join(tokens)
    assert "kimi-k3" in full_text.lower()


def test_chat_stream_validation_error(fixture_client):
    # Empty messages list should return 422
    response = fixture_client.post(
        "/api/v1/chat/stream",
        json={"messages": []},
    )
    assert response.status_code == 422


def test_template_catalog_and_unknown_template(fixture_client):
    catalog = fixture_client.get('/api/v1/chat/templates').json()['templates']
    assert len(catalog) >= 15
    assert all(item['default_prompt'] and item['preview_url'] for item in catalog)
    response = fixture_client.post('/api/v1/chat/stream', json={
        'messages': [{'role': 'user', 'content': '报告'}], 'template_id': '../../settings',
    })
    assert response.status_code == 422


def model_client(monkeypatch, tmp_path, responses):
    from copy import deepcopy
    from just_read import chat
    calls = []

    class Upstream:
        status_code = 200
        def __init__(self, content): self.content = content
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def aiter_lines(self):
            if isinstance(self.content, list):
                for chunk in self.content:
                    yield 'data: ' + (chunk if isinstance(chunk, str) else json.dumps(chunk))
            else:
                yield 'data: ' + json.dumps({'choices': [{'delta': {'content': self.content}}]})
                yield 'data: [DONE]'

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, method, url, **kwargs):
            calls.append(deepcopy(kwargs['json']))
            return Upstream(responses[len(calls)-1])

    monkeypatch.setattr(chat.httpx, 'AsyncClient', Client)
    app = create_app(Settings(test_mode=True, llm_provider='openrouter', llm_model='moonshotai/kimi-k3',
                              llm_api_key='test-placeholder', database_path=str(tmp_path/'model.sqlite3')))
    return TestClient(app), calls


def test_selected_source_custom_prompt_and_bounded_continuation(monkeypatch, tmp_path):
    first = '<!doctype html><html><head><title>RAG</title></head><body><h1>企业知识库</h1>'
    last = '</body></html><script>document.title="企业知识库";</script>'
    client, calls = model_client(monkeypatch, tmp_path, [first, last])
    with client:
        client.get('/api/v1/health')
        result = client.post('/api/v1/chat/stream', json={
            'messages': [{'role': 'user', 'content': '企业知识库报告：检索与生成分工。'}],
            'template_id': 'storybook', 'template_prompt': '保留纸感，但标题使用深绿色。',
        })
    assert len(calls) == 2  # The trailing title script is already a complete artifact.
    assert calls[0]['max_tokens'] == 24000
    assert calls[0]['reasoning'] == {'effort': 'low'}
    assert '标题使用深绿色' in calls[0]['messages'][0]['content']
    assert any('设计参考源码' in message['content'] for message in calls[0]['messages'])
    assert calls[0]['messages'][-1]['content'].startswith('企业知识库报告')
    assert calls[1]['messages'][-2]['content'] == first
    assert result.text.count('data: [DONE]') == 1
    assert 'event: status' in result.text


def test_prompt_only_does_not_send_source(monkeypatch, tmp_path):
    client, calls = model_client(monkeypatch, tmp_path, ['<!doctype html><html><head></head><body>新的报告</body></html>'])
    with client:
        client.get('/api/v1/health')
        response = client.post('/api/v1/chat/stream', json={
            'messages': [{'role': 'user', 'content': '新的报告'}], 'template_id': 'fluorescent',
            'template_reference': False,
        })
    assert response.status_code == 200
    assert len(calls) == 1
    assert len(calls[0]['messages']) == 2
    assert '#04070a' in calls[0]['messages'][0]['content']
    assert '设计参考源码' not in str(calls[0]['messages'])


def test_incomplete_html_stops_after_three_calls(monkeypatch, tmp_path):
    client, calls = model_client(monkeypatch, tmp_path, ['<!doctype html><html>', '<body>', '<h1>未完成'])
    with client:
        client.get('/api/v1/health')
        result = client.post('/api/v1/chat/stream', json={
            'messages': [{'role': 'user', 'content': '报告'}], 'template_id': 'industrial',
        })
    assert len(calls) == 3
    assert 'OUTPUT_INCOMPLETE' in result.text
    assert 'data: [DONE]' not in result.text


def empty_round(reason='length'):
    return [
        {'choices': [{'delta': {'reasoning': 'private reasoning'}}]},
        {'choices': [{'delta': {}, 'finish_reason': reason}],
         'usage': {'completion_tokens': 24000, 'completion_tokens_details': {'reasoning_tokens': 24000}}},
        '[DONE]',
    ]


def test_reasoning_only_retries_once_without_empty_history(monkeypatch, tmp_path):
    html = '<!doctype html><html><body>奶龙介绍</body></html>'
    client, calls = model_client(monkeypatch, tmp_path, [empty_round(), html])
    with client:
        client.get('/api/v1/health')
        result = client.post('/api/v1/chat/stream', json={
            'messages': [{'role': 'user', 'content': '帮我生成一份介绍奶龙的报告'}],
            'template_id': 'warm-illustrated',
        })
    assert len(calls) == 2
    assert calls[1]['messages'] == calls[0]['messages']
    assert calls[1]['max_tokens'] == 32000
    assert 'thinking' in result.text and 'retrying' in result.text
    assert 'private reasoning' not in result.text
    assert html in result.text and 'data: [DONE]' in result.text


def test_empty_retries_are_bounded_and_report_budget(monkeypatch, tmp_path, caplog):
    client, calls = model_client(monkeypatch, tmp_path, [empty_round(), empty_round()])
    with client:
        client.get('/api/v1/health')
        result = client.post('/api/v1/chat/stream', json={
            'messages': [{'role': 'user', 'content': '报告'}], 'template_id': 'warm-illustrated',
        })
    assert len(calls) == 2
    assert 'OUTPUT_BUDGET_EXHAUSTED' in result.text and 'data: [DONE]' not in result.text
    assert 'reasoning_tokens=24000' in caplog.text
    assert 'private reasoning' not in caplog.text


def test_explicit_budget_is_not_silently_increased(monkeypatch, tmp_path):
    client, calls = model_client(monkeypatch, tmp_path, [empty_round()])
    with client:
        client.get('/api/v1/health')
        result = client.post('/api/v1/chat/stream', json={
            'messages': [{'role': 'user', 'content': '报告'}], 'max_tokens': 100,
        })
    assert len(calls) == 1 and calls[0]['max_tokens'] == 100
    assert 'OUTPUT_BUDGET_EXHAUSTED' in result.text


@pytest.mark.parametrize('finish', [None, 'error', 'content_filter'])
def test_failed_or_unfinished_stream_is_not_success(monkeypatch, tmp_path, finish):
    client, calls = model_client(monkeypatch, tmp_path, [[{
        'choices': [{'delta': {'content': '<html>partial'}, 'finish_reason': finish}],
    }]])
    with client:
        client.get('/api/v1/health')
        result = client.post('/api/v1/chat/stream', json={
            'messages': [{'role': 'user', 'content': '报告'}],
        })
    assert len(calls) == 1
    assert 'partial' in result.text and 'event: error' in result.text
    assert 'data: [DONE]' not in result.text
