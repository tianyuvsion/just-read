import hashlib
import io
import json
import re
import subprocess
import struct
import sys
from zipfile import ZipFile

from just_read.publishing import digest, package_report, prepare_report, render_html, report_body, scene_glb


def sample():
    return {"id": "r1", "version": 1, "source": "generated", "title": "离线研究", "question": "问题", "summary": "结论", "category": "研究", "minutes": 1,
        "createdAt": "2026-10-07T00:00:00+00:00", "chapters": [{"id": "overview", "title": "概览", "paragraphs": ["正文 [S1]"]}],
        "bookmarks": [], "chart": [{"label": "样例", "value": -1.0}],
        "workflow": {"visuals": [{"kind": "flow", "id": "flow", "title": "流程", "nodes": [{"id": "a", "label": "A"}], "edges": []}],
            "scene": {"id": "scene", "kind": "schematic", "parts": [{"id": "part", "label": "部件", "geometry": "box", "position": [0, 0, 0], "size": [1, 1, 1], "dimensions_known": False}]}}}


def test_check_warnings_keep_readable_report_and_bind_hashes():
    original = sample()
    output, evidence = prepare_report(original, {"sources": []}, {"question": "问题"})
    assert output["chapters"] == original["chapters"]
    assert output["workflow"]["quality_review"]["status"] == "warnings"
    assert output["workflow"]["quality_review"]["human_status"] == "not_reviewed"
    assert output["workflow"]["manifest"]["body_sha256"] == digest(report_body(output))
    assert output["workflow"]["manifest"]["evidence_sha256"] == digest(evidence)
    assert "manifest" not in original["workflow"]
    same, same_evidence = prepare_report(output, evidence, {"question": "问题"})
    assert same == output and same_evidence == evidence


def test_bounded_repairs_do_not_turn_missing_evidence_into_a_quality_gate():
    source = sample()
    source["chapters"].append({"id": "overview", "title": "", "paragraphs": ["仍保留"]})
    source["bookmarks"] = ["nonexistent", "overview", "overview"]
    source["chart"].append({"label": "bad", "value": float("inf")})
    result, evidence = prepare_report(source, {})
    assert len(set(c["id"] for c in result["chapters"])) == 2
    assert result["bookmarks"] == ["overview"]
    assert result["chart"] == [{"label": "样例", "value": -1.0}]
    assert result["chartMeta"]["unit"] == "未标注单位"
    assert result["workflow"]["quality_review"]["repair_count"] > 0


def test_offline_script_escapes_html_and_has_no_remote_assets():
    source = sample()
    source["chapters"][0]["paragraphs"] = ['</script><script>window.bad=true</script> & 中文']
    report, evidence = prepare_report(source, {"sources": [{"id": "S1", "raw_content": "文献"}]})
    document = render_html(report, evidence)
    assert '<script>window.bad=true</script>' not in document
    assert not re.search(r'<(?:script|link|img)[^>]+(?:src|href)=["\']https?://', document)
    embedded = re.search(r'<script id="justread-data" type="application/json">(.*?)</script>', document, re.S)
    payload = json.loads(embedded.group(1))
    assert payload["report"]["chapters"][0]["paragraphs"] == source["chapters"][0]["paragraphs"]
    assert hashlib.sha256(payload["body_json"].encode()).hexdigest() == report["workflow"]["manifest"]["body_sha256"]
    assert json.loads(payload["body_json"])["chart"][0]["value"] == -1.0


def test_packaged_files_manifest_and_standalone_verifier(tmp_path):
    report, evidence = prepare_report(sample(), {"sources": []})
    blob, manifest = package_report(report, evidence)
    with ZipFile(io.BytesIO(blob)) as archive:
        assert set(archive.namelist()) == {"index.html", "report.json", "evidence.json", "reviews.json", "manifest.json", "verify.py", "README.txt", "scene.glb", "scene.json"}
        assert "manifest.json" not in manifest["files"]
        for name, item in manifest["files"].items():
            assert hashlib.sha256(archive.read(name)).hexdigest() == item["sha256"]
        archive.extractall(tmp_path)
    result = subprocess.run([sys.executable, str(tmp_path / "verify.py")], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    (tmp_path / "report.json").write_text("tampered")
    corrupted = subprocess.run([sys.executable, str(tmp_path / "verify.py")], capture_output=True, text=True)
    assert corrupted.returncode != 0 and "hash mismatch" in corrupted.stderr


def test_scene_glb_contains_actual_meshes_and_no_external_buffers():
    parts = [{"id": kind, "label": kind, "geometry": kind, "position": [i*2, 0, 0], "size": [1, 2, 1], "dimensions_known": False} for i, kind in enumerate(("box", "sphere", "cylinder"))]
    blob = scene_glb({"parts": parts, "relations": [{"source": "box", "target": "sphere", "type": "connects"}]})
    magic, version, length = struct.unpack_from("<III", blob)
    assert magic == 0x46546C67 and version == 2 and length == len(blob)
    json_length, chunk_type = struct.unpack_from("<II", blob, 12)
    assert chunk_type == 0x4E4F534A
    scene = json.loads(blob[20:20+json_length])
    assert len(scene["nodes"]) == 3 and len(scene["meshes"]) == 3
    assert all("uri" not in buffer for buffer in scene["buffers"])
    binary_length, binary_type = struct.unpack_from("<II", blob, 20+json_length)
    assert binary_type == 0x004E4942 and binary_length == scene["buffers"][0]["byteLength"]
    for mesh in scene["meshes"]:
        primitive = mesh["primitives"][0]
        assert scene["accessors"][primitive["attributes"]["POSITION"]]["count"] >= 8
        assert scene["accessors"][primitive["indices"]]["count"] >= 36
    assert all(n["extras"]["dimensions_known"] is False for n in scene["nodes"])


def test_offline_review_snapshot_does_not_change_report_hash():
    report, evidence = prepare_report(sample(), {})
    reviews = [{"kind": "human", "version": 1, "notes": "已检查", "decision": "approved"}]
    blob, manifest = package_report(report, evidence, reviews)
    with ZipFile(io.BytesIO(blob)) as archive:
        assert json.loads(archive.read("reviews.json")) == reviews
        exported = json.loads(archive.read("report.json"))
        assert exported["workflow"]["manifest"]["body_sha256"] == report["workflow"]["manifest"]["body_sha256"]
    assert manifest["review_snapshot_hash"] == digest(reviews)
