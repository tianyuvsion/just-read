#!/usr/bin/env python3
"""Explicitly opt-in real A–D acceptance with isolated storage and simulated inputs.

Business HTTP uses TestClient; upstream model/search calls are real. No secrets,
session cookies, provider payloads or share tokens are written to results.json.
"""
from __future__ import annotations

import base64
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
import hashlib
import importlib.util
import io
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
from zipfile import ZipFile
import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
spec = importlib.util.spec_from_file_location("live_helpers", ROOT / "scripts/test-live-api.py")
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)

MATERIAL = """# 无刷直流电机：软件验收用模拟材料
本材料为自建软件测试输入。所有数值均为模拟验收数据，不是实测性能，不可用于采购或工程选型。

概念部件包括：圆柱形外壳、固定在外壳内的定子、定子中心的转子、连接转子的轴、支撑轴的轴承，以及位于外壳端部的位置传感器。只描述概念布局，所有真实尺寸、比例、间距均未知。

能量传递路径：电源向控制器供电，控制器向定子绕组提供电流，定子的旋转磁场对转子施加转矩，转子通过轴向负载输出机械能。位置传感器向控制器反馈转子位置。轴承支撑轴，外壳固定定子并保护内部部件。

同一模拟测试时期 T0，方案A的输入功率为20 W，效率为85 %。

同一模拟测试时期 T0，方案B的输入功率为30 W，效率为90 %。

模拟验收预期：A与B输入功率相加为50 W；B效率减A效率为5个百分点。这两项仅检查算术，并不证明能耗可以合并或性能真实性。

非研究内容：UNRELATED_LOCAL_TEST_MARKER_7631 是未引用原文隔离测试标记，不属于研究结论或部件描述。
"""


def main():
    from fastapi.testclient import TestClient
    from just_read.settings import Settings
    from just_read.publishing import digest, report_body
    settings = Settings.from_env()
    helpers.check_settings(settings, "full")
    directory = ROOT / ".test-data" / ("live-ad-" + str(uuid.uuid4()))
    directory.mkdir(mode=0o700, parents=True)
    database = directory / "acceptance.sqlite3"
    os.close(os.open(database, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
    live = replace(settings, database_path=str(database), workers=1)
    result = {"model": settings.llm_model, "provider": settings.llm_provider,
              "test_transport": "FastAPI TestClient; real upstream network",
              "input_data": "simulated software acceptance, not measured research data",
              "checks": {}, "coverage": {}}
    started = time.monotonic()

    def check(name, condition):
        result["checks"][name] = bool(condition)
        if not condition:
            raise AssertionError(name)

    def save():
        result["elapsed_seconds"] = round(time.monotonic() - started, 2)
        (directory / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    def body(response):
        return helpers.response_body(response)

    task_id, terminal = None, False
    try:
        with helpers.isolated_app_import(database) as create_app:
            app = create_app(live)
        with TestClient(app, base_url="https://testserver", raise_server_exceptions=False) as client:
            check("configured_production", body(client.get("/api/v1/health"))["ready"] is True)
            source = body(client.post("/api/v1/sources", json={"name": "motor-simulated.md", "media_type": "text/markdown",
                "content_base64": base64.b64encode(MATERIAL.encode()).decode()}))
            check("original_material_download", client.get(f"/api/v1/sources/{source['id']}/download").content == MATERIAL.encode())
            check("source_chunks_located", bool(source["chunks"]) and all("start" in c and "end" in c for c in source["chunks"]))
            request = {"question": "基于上传材料说明无刷直流电机的主要部件和能量传递路径；比较方案A/B功率与效率，并调用数值工具核算功率合计和效率差。明确所有数值为模拟验收数据，外部检索仅补充原理，不能把模拟数据当实测性能。", "depth": "brief", "source_ids": [source["id"]],
                "reader": "软件验收人员与工程师", "scope": "限概念部件、能量路径和给定模拟数据；未知尺寸需明确；不引用无关标记", "enable_3d": True}
            task = body(client.post("/api/v1/research-tasks", json=request, headers={"Idempotency-Key": str(uuid.uuid4())}))
            task_id = task["id"]
            result["task_id"] = task_id
            helpers.emit("live_ad_started", task_id=task_id, directory=str(directory.relative_to(ROOT)))
            previous = None
            deadline = time.monotonic() + live.task_timeout_seconds
            while task["status"] not in {"succeeded", "failed", "cancelled"}:
                runs = body(client.get(f"/api/v1/research-tasks/{task_id}/runs"))["runs"]
                stages = [r for r in runs if r["kind"] in {"stage", "checkpoint", "usage"}]
                current = (task["status"], task["step"], stages[-1].get("stage") if stages else None,
                           stages[-1].get("status") if stages else None)
                if current != previous:
                    helpers.emit("live_ad_progress", status=current[0], step=current[1], stage=current[2], stage_status=current[3])
                    previous = current
                if time.monotonic() > deadline:
                    helpers.fail("TASK_TIMEOUT")
                time.sleep(1)
                task = body(client.get(f"/api/v1/research-tasks/{task_id}"))
            terminal = True
            result["generation_seconds"] = round(time.monotonic() - started, 2)
            result["task_status"] = task["status"]
            if task["status"] != "succeeded":
                helpers.fail((task.get("error") or {}).get("code", "GENERATION_FAILED"))
            rid = task["report"]["id"]
            result["report_id"] = rid
            base = f"/api/v1/reports/{rid}"
            original = body(client.get(base + "/versions/1"))
            wf = original["workflow"]
            runs = body(client.get(f"/api/v1/research-tasks/{task_id}/runs"))["runs"]
            (directory / "runs.json").write_text(json.dumps(runs, ensure_ascii=False, indent=2), encoding="utf-8")
            check("generated_version1", original["source"] == "generated" and original["version"] == 1 and wf["schema_version"] == "1.0")
            check("body_manifest", wf["manifest"]["body_sha256"] == digest(report_body(original)))
            check("asset_manifest", all(digest(wf[k]) == v for k, v in wf["manifest"]["asset_hashes"].items()))
            result["chapters"] = len(original["chapters"])
            result["warnings"] = wf.get("validation", {}).get("warnings", [])
            result["source_count"] = len(wf["sources"])
            result["evidence_count"] = len(wf["evidence"])
            result["visual_kinds"] = [v.get("kind") for v in wf.get("visuals", [])]
            with app.state.store.connect() as db:
                evidence = json.loads(db.execute("SELECT content FROM report_artifacts WHERE report_id=?", (rid,)).fetchone()["content"])
            check("private_evidence_manifest", wf["manifest"]["evidence_sha256"] == digest(evidence))
            network_sources = [s for s in evidence["sources"] if s.get("kind") != "upload" and s.get("url", "").startswith("https://") and len(s.get("raw_content", "")) >= 80]
            result["coverage"]["network_extracts"] = len(network_sources)
            computed = [c for c in wf.get("calculations", []) if c.get("status") == "computed"]
            result["coverage"]["automatic_calculations"] = len(computed)
            result["automatic_calculations"] = [{k: c.get(k) for k in ("operation", "result_decimal", "unit", "formula")} for c in computed]
            result["coverage"]["calculation_in_conclusions"] = bool(computed) and all(any(c["id"] in f.get("calculation_refs", []) for f in wf["research_ir"]["claims"]) for c in computed)
            check("automatic_sum_50W", any(c["operation"] == "sum" and c["unit"] == "W" and Decimal(c["result_decimal"]) == 50 for c in computed))
            check("automatic_difference_5_percentage_points", any(c["operation"] == "difference" and c["unit"] == "百分点" and Decimal(c["result_decimal"]) == 5 for c in computed))
            check("calculations_in_conclusions", result["coverage"]["calculation_in_conclusions"])
            parts = (wf.get("scene") or {}).get("parts", [])
            result["coverage"]["scene_parts"] = len(parts)
            result["scene_labels"] = [p.get("label") for p in parts]
            quality = wf.get("ai_quality_review", {})
            result["coverage"]["final_model_review"] = quality.get("status") == "completed" and quality.get("simulated") is False and quality.get("report_version") == 1
            check("final_model_review_completed", result["coverage"]["final_model_review"])
            check("scene_with_verified_evidence", bool(parts) and all(p["evidence_refs"] and all(any(e["id"] == eid and e.get("verification", {}).get("quote_match") for e in wf["evidence"]) for eid in p["evidence_refs"]) for p in parts))
            check("real_scene_and_quality_usage", all(any(r["kind"] == "usage" and r.get("stage") == stage and r.get("output_tokens", 0) > 0 for r in runs) for stage in ("C.scene", "D.quality_model")))
            result["coverage"]["automatic_edits_applied"] = len(wf.get("auto_revision", {}).get("applied", []))
            result["usage"] = [{k: r.get(k) for k in ("stage", "kind", "operation", "status", "input_tokens", "output_tokens", "elapsed_ms", "error_code", "cost") if k in r} for r in runs if r["kind"] == "usage"]
            (directory / "report-v1.json").write_text(json.dumps(original, ensure_ascii=False, indent=2), encoding="utf-8")
            helpers.emit("live_ad_generated", chapters=result["chapters"], network_sources=len(network_sources), calculations=len(computed), scene_parts=len(parts), final_model_review=result["coverage"]["final_model_review"], warnings=len(result["warnings"]))

            # Manual calculation is a distinct coverage item from automatic planning.
            power = []
            for ds in wf.get("datasets", []):
                if ds.get("unit", "").lower() in {"w", "瓦", "瓦特"}:
                    rows = ds.get("rows", [])
                    by_value = {Decimal(str(r.get("value", 0))): str(r.get("id") or f"{ds['id']}:{i}") for i, r in enumerate(rows)}
                    if Decimal(20) in by_value and Decimal(30) in by_value:
                        power = [by_value[Decimal(20)], by_value[Decimal(30)]]
                        break
            latest = original
            if power:
                calculated = body(client.post(base + "/actions", json={"action": "calculate", "base_version": 1, "operation": "sum", "input_refs": power}))
                check("manual_calculation_50W", Decimal(calculated["calculation"]["result_decimal"]) == Decimal(50))
                latest = calculated["report"]
                result["coverage"]["manual_calculation"] = True
            else:
                result["coverage"]["manual_calculation"] = False
            edited = deepcopy(latest)
            edited["title"] += " · 本地验收修订"
            latest = body(client.post(base + "/versions", json={"base_version": latest["version"], "report": edited}))
            check("edit_immutable_version", body(client.get(base + "/versions/1")) == original)
            stale = client.post(base + "/versions", json={"base_version": 1, "report": edited})
            check("stale_edit_409", stale.status_code == 409)
            check("automatic_consistency_review", body(client.post(base + "/actions", json={"action": "validate", "version": latest["version"]}))["kind"] == "automatic")
            helpers.emit("live_ad_d_review_started", version=latest["version"])
            ai_response = client.post(base + "/actions", json={"action": "review", "version": latest["version"]})
            if ai_response.is_success:
                ai = body(ai_response)
                check("real_version_AI_review", ai["kind"] == "ai" and ai["simulated"] is False and ai["version"] == latest["version"])
            else:
                result["coverage"]["version_AI_review_error"] = ai_response.json().get("error", {}).get("code", "HTTP_CHECK_FAILED")
            human = body(client.post(base + "/actions", json={"action": "human-review", "version": latest["version"], "reviewer": "local software acceptance", "decision": "reviewed", "notes": "仅检查软件流程及模拟输入，未完成工程事实审查。"}))
            check("human_review_bound", human["kind"] == "human" and human["version"] == latest["version"])
            shared = body(client.post(base + "/actions", json={"action": "publish", "version": latest["version"], "ttl_seconds": 3600}))
            def anonymous():
                client.cookies.clear()
                return body(client.get("/api/v1/shared/" + shared["token"]))
            cookies = httpx.Cookies(client.cookies)
            public = anonymous()
            client.cookies.update(cookies)
            check("anonymous_frozen_share", public["version"] == latest["version"] and public["title"] == latest["title"])
            check("public_manifest", digest(report_body(public)) == public["workflow"]["manifest"]["body_sha256"] and public["workflow"]["manifest"]["origin_body_sha256"] == latest["workflow"]["manifest"]["body_sha256"])
            check("share_excludes_originals", all("pages" not in s and "raw_content" not in s and all("text" not in c for c in s.get("chunks", [])) for s in public["workflow"]["sources"]))
            latest = body(client.post(base + "/actions", json={"action": "restore", "version": 1, "base_version": latest["version"]}))
            check("restore_creates_version", latest["title"] == original["title"] and latest["version"] > public["version"])
            check("share_stays_frozen", body(client.get("/api/v1/shared/" + shared["token"])) == public)
            body(client.post(base + "/actions", json={"action": "revoke-share", "token": shared["token"]}))
            check("revoke_404", client.get("/api/v1/shared/" + shared["token"]).status_code == 404)
            blob = client.get(base + "/export?version=1&format=zip")
            check("zip_download", blob.status_code == 200)
            (directory / "report-v1.zip").write_bytes(blob.content)
            with ZipFile(io.BytesIO(blob.content)) as archive:
                manifest = json.loads(archive.read("manifest.json"))
                check("zip_files_hashes", all(hashlib.sha256(archive.read(n)).hexdigest() == m["sha256"] and len(archive.read(n)) == m["size_bytes"] for n, m in manifest["files"].items()))
                check("export_fixed_version", json.loads(archive.read("report.json")) == original)
                archive.extractall(directory / "offline")
                result["zip_file_count"] = len(archive.namelist())
            verify = subprocess.run([sys.executable, str(directory / "offline/verify.py")], capture_output=True)
            check("verify_script_passed", verify.returncode == 0)
            report_file = directory / "offline/report.json"
            raw = report_file.read_bytes()
            report_file.write_bytes(raw + b" ")
            check("verify_detects_tampering", subprocess.run([sys.executable, str(directory / "offline/verify.py")], capture_output=True).returncode != 0)
            report_file.write_bytes(raw)
            result["versions"] = len(body(client.get(base + "/versions"))["versions"])
            html_response = client.get(base + "/export?version=1&format=html")
            check("html_download", html_response.status_code == 200 and b'id="justread-data"' in html_response.content)
            (directory / "report-v1.html").write_bytes(html_response.content)
            result["status"] = "completed"
    except helpers.PipelineError as exc:
        result["status"] = "failed"
        result["error_code"] = exc.code if exc.code in helpers.ERRORS else "GENERATION_FAILED"
    except AssertionError as exc:
        result["status"] = "failed"
        result["failed_check"] = str(exc)
    except Exception as exc:
        result["status"] = "failed"
        result["exception_type"] = type(exc).__name__
    finally:
        save()
        helpers.emit("live_ad_result", status=result.get("status"), passed=sum(result["checks"].values()), total=len(result["checks"]), directory=str(directory.relative_to(ROOT)))
    return 0 if result.get("status") == "completed" else 1


if __name__ == "__main__":
    if sys.argv[1:] != ["--run"]:
        print("Opt-in real API test: use --run (may incur upstream charges).")
        raise SystemExit(2)
    logging.disable(logging.CRITICAL)
    raise SystemExit(main())
