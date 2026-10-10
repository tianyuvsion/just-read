"""Percentage-point regression across automatic research and manual calculation."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from just_read.app import create_app
from just_read.pipeline import Pipeline
from test_pipeline import Workflow, config, noop
from test_workspace import report as base_report
from test_workspace_api import settings


QUOTE = "2026年方案效率：方案A 85 %；方案B 90 %。"


@pytest.mark.asyncio
async def test_automatic_percentage_difference_flows_to_claims_and_model_review_with_point_unit():
    def analysis(result):
        result["data"] = [{"label": label, "value_text": value, "unit": "%", "metric": "方案效率", "period": "2026年",
                           "source_id": "S1", "quote": QUOTE} for label, value in [("方案A", "85"), ("方案B", "90")]]
        result["calculations"] = [{"operation": "difference", "labels": ["方案A", "方案B"]}]
    workflow, reviews = Workflow(mutate_analysis=analysis, raw=QUOTE), []
    def handler(request):
        body = json.loads(request.content)
        if request.url.host != "api.tavily.com" and body["text"]["format"]["name"] == "Review":
            reviews.append(json.loads(body["input"][1]["content"]))
        return workflow(request)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await Pipeline(config(), client).run("对比方案效率差值", "brief", noop, lambda: False)
    assert reviews and all(c["unit"] == "百分点" for c in reviews[0]["calculations"])
    calculations = result["workflow"]["calculations"]
    assert calculations and all(c["result"] == 5 and c["unit"] == "百分点" for c in calculations)
    claims = [c for c in result["workflow"]["research_ir"]["claims"] if c["calculation_refs"]]
    assert claims and all("= 5 百分点" in c["text"] for c in claims)
    assert any("= 5 百分点" in p for chapter in result["chapters"] for p in chapter["paragraphs"])
    assert all(item["unit"] == "%" for calculation in calculations for item in calculation["inputs"])


def test_manual_percentage_difference_uses_points_in_api_version_and_report_text(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        client.get("/api/v1/health")
        report = base_report()
        report["workflow"] = {"datasets": [{"id": "efficiency", "unit": "%", "period": "2026年", "rows": [
            {"id": "a", "label": "方案A效率", "value_text": "85", "source_id": "S1"},
            {"id": "b", "label": "方案B效率", "value_text": "90", "source_id": "S1"}]}]}
        saved = client.post("/api/v1/reports/import", json=report)
        assert saved.status_code == 200, saved.text
        rid = saved.json()["id"]
        response = client.post(f"/api/v1/reports/{rid}/actions", json={"action": "calculate", "base_version": 1,
            "operation": "difference", "input_refs": ["a", "b"]})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["calculation"]["result"] == 5 and body["calculation"]["unit"] == "百分点"
        assert all(row["unit"] == "%" for row in body["calculation"]["inputs"])
        assert body["report"]["version"] == 2
        assert any("结果：5 百分点" in p for c in body["report"]["chapters"] for p in c["paragraphs"])
        assert client.get(f"/api/v1/reports/{rid}/versions/2").json()["workflow"]["calculations"][-1]["unit"] == "百分点"
