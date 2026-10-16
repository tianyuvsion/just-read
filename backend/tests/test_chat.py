import json
import pytest
from fastapi.testclient import TestClient

from just_read.app import create_app
from just_read.settings import Settings


@pytest.fixture
def fixture_client():
    settings = Settings(
        test_mode=True,
        llm_provider="fixture",
        llm_model="moonshotai/kimi-k3",
        fixture_delay_seconds=0.001,
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
