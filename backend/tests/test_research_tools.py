from copy import deepcopy
import pytest

from just_read.research_tools import build_workflow, calculate, locate_support, normalize_scene, public_source, select_source


def data():
    return {"D1": {"label": "基期", "value_text": "20", "unit": "次", "source_id": "S1"},
            "D2": {"label": "当期", "value_text": "30", "unit": "次", "source_id": "S2"}}


@pytest.mark.parametrize("operation,result,unit", [
    ("sum", 50, "次"), ("mean", 25, "次"), ("difference", 10, "次"),
    ("ratio", 2/3, "ratio"), ("percent_change", 50, "%"), ("min", 20, "次"), ("max", 30, "次"),
])
def test_allowlisted_calculation_preserves_input_and_formula_provenance(operation, result, unit):
    original = data()
    calculation = calculate({"operation": operation, "labels": ["基期", "当期"]}, original, "C1")
    assert calculation["status"] == "computed" and calculation["result"] == pytest.approx(result)
    assert calculation["unit"] == unit and calculation["formula"]
    assert calculation["source_refs"] == ["S1", "S2"]
    assert [item["data_id"] for item in calculation["inputs"]] == ["D1", "D2"]
    assert original == data()


@pytest.mark.parametrize("unit", ["%", "％", "percent", "percentage", "百分比"])
def test_percentage_difference_is_points_and_preserves_percentage_input_units(unit):
    values = {"D1": {"label": "方案A效率", "value_text": "85", "unit": unit, "source_id": "S1"},
              "D2": {"label": "方案B效率", "value_text": "90", "unit": unit, "source_id": "S1"}}
    original = deepcopy(values)
    result = calculate({"operation": "difference", "input_refs": ["D1", "D2"]}, values, "C1")
    assert result["status"] == "computed" and result["result_decimal"] == "5" and result["unit"] == "百分点"
    assert [item["unit"] for item in result["inputs"]] == [unit, unit]
    assert values == original
    reverse = calculate({"operation": "difference", "input_refs": ["D2", "D1"]}, values, "C2")
    assert reverse["result"] == -5 and reverse["unit"] == "百分点"


@pytest.mark.parametrize("operation,expected,unit", [
    ("percent_change", (90 - 85) / 85 * 100, "%"),
    ("sum", 175, "%"), ("mean", 87.5, "%"), ("min", 85, "%"), ("max", 90, "%"),
    ("ratio", 85 / 90, "ratio"),
])
def test_percentage_relative_change_and_other_operations_keep_their_own_units(operation, expected, unit):
    values = {"D1": {"label": "A", "value_text": "85", "unit": "%"},
              "D2": {"label": "B", "value_text": "90", "unit": "%"}}
    result = calculate({"operation": operation, "input_refs": ["D1", "D2"]}, values, "C1")
    assert result["status"] == "computed" and result["result"] == pytest.approx(expected)
    assert result["unit"] == unit


def test_percent_suffixed_values_without_explicit_unit_still_produce_point_difference():
    values = {"D1": {"label": "A", "value_text": "85%", "unit": ""},
              "D2": {"label": "B", "value_text": "90％", "unit": ""}}
    result = calculate({"operation": "difference", "input_refs": ["D1", "D2"]}, values, "C1")
    assert result["status"] == "computed" and result["result"] == 5 and result["unit"] == "百分点"
    assert [item["value_text"] for item in result["inputs"]] == ["85%", "90％"]


@pytest.mark.parametrize("operation,refs,mutation,reason", [
    ("eval", ["D1"], {}, "unsupported_operation_or_inputs"),
    ("sum", ["unknown"], {}, "unknown_or_ambiguous_input"),
    ("sum", ["D1", "D2"], {"D2": {"unit": "人"}}, "incompatible_units"),
    ("ratio", ["D1", "D2"], {"D2": {"value_text": "0"}}, "division_by_zero"),
    ("sum", ["D1"], {"D1": {"value_text": "1,2,3"}}, "ambiguous_number"),
])
def test_invalid_calculation_does_not_eval_or_invent_zero(operation, refs, mutation, reason):
    values = data()
    for key, fields in mutation.items(): values[key].update(fields)
    result = calculate({"operation": operation, "input_refs": refs}, values, "C1")
    assert result["status"] == "unavailable" and result["result"] is None and result["reason"] == reason


def test_quote_locator_retains_original_page_and_character_span():
    source = {"id": "uploaded-uuid", "chunks": [
        {"id": "page2-p1", "text": "前文。这里是引用原文。后文。", "page": 2, "paragraph": 1, "start": 100, "end": 114}]}
    evidence = locate_support({"source_id": source["id"], "quote": "这里是引用原文。"}, [source], "E1")
    assert evidence["verification"] == {"quote_match": True, "exact_span": True}
    assert evidence["chunk_id"] == "page2-p1"
    assert evidence["locator"] == {"page": 2, "paragraph": 1, "start": 103, "end": 111}
    missing = locate_support({"source_id": source["id"], "quote": "模型改写的引用"}, [source], "E2")
    assert missing["chunk_id"] is None and missing["verification"]["quote_match"] is False


def test_model_excerpt_selection_does_not_erase_original_chunks():
    source = {"id": "doc", "chunks": [
        {"id": "one", "text": "irrelevant " * 50, "page": 1, "start": 0, "end": 550},
        {"id": "two", "text": "TaskGroup cancellation exceptions", "page": 2, "start": 550, "end": 583}]}
    result = select_source(source, "TaskGroup cancellation", limit=40)
    assert result["raw_content"].startswith("TaskGroup")
    assert result["chunks"] == source["chunks"] and len(result["raw_content"]) <= 40


def test_uncited_public_source_has_only_locator_metadata_and_selection_boundary():
    source = {"id": "doc", "kind": "upload", "pages": [{"text": "未引用私有段落"}], "chunks": [
        {"id": "c1", "text": "选择这个片段但不引用。", "page": 1, "paragraph": 1, "start": 0, "end": 12},
        {"id": "c2", "text": "未引用私有段落", "page": 2, "paragraph": 1, "start": 20, "end": 28}]}
    internal = select_source(source, "选择", limit=5)
    public = public_source(internal, [])
    assert public["excerpts"] == []
    assert all(chunk["quotes"] == [] and "text" not in chunk for chunk in public["chunks"])
    assert public["selection"]["selected_chunk_ids"] == ["c1"]
    assert public["selection"]["selected_characters"] == 5
    assert public["selection"]["selected_ranges"] == [
        {"chunk_id": "c1", "page": 1, "paragraph": 1, "start": 0, "end": 5, "characters": 5}]
    assert public["selection"]["quoted_evidence_count"] == 0
    assert "本来源未被引用" in public["selection"]["notice"]
    assert internal["chunks"] == source["chunks"] and internal["pages"] == source["pages"]


def test_scene_only_uses_locatable_evidence_and_normalized_geometry():
    raw = {"parts": [
        {"id": "base", "label": "底座", "geometry": "box", "position": [100, 0, 0], "size": [3, 2, 1], "evidence_refs": ["E1"]},
        {"id": "fake", "label": "无依据部件", "geometry": "sphere", "evidence_refs": ["E2"]}],
        "relations": [{"source": "base", "target": "fake", "type": "supports"}]}
    evidence = [{"id": "E1", "verification": {"quote_match": True}}, {"id": "E2", "verification": {"quote_match": False}}]
    scene = normalize_scene(raw, evidence)
    assert len(scene["parts"]) == 1 and scene["relations"] == []
    assert scene["parts"][0]["position"] == [10, 0, 0]
    assert scene["parts"][0]["dimensions_known"] is False and scene["unknowns"]
    assert scene["provenance"]["mode"] == "conceptual"


def test_typed_chart_specs_preserve_numeric_conflicts_and_explicit_time_order():
    rows = {"D1": {"label": "甲", "value_text": "20", "unit": "次", "metric": "计数", "period": "2025", "source_id": "S1"},
            "D2": {"label": "甲", "value_text": "30", "unit": "次", "metric": "计数", "period": "2026", "source_id": "S2"}}
    arguments = ({"question": "变化"}, [], {"subquestions": []}, {}, rows, {"decisions": []}, {"sections": []}, [])
    workflow = build_workflow(*arguments)
    line = next(visual for visual in workflow["visuals"] if visual["kind"] == "line")
    assert line["labels"] == ["2025", "2026"] and line["series"][0]["values"] == [20, 30]
    assert {visual["kind"] for visual in workflow["visuals"]} == {"bar", "table", "line"}
    rows["D3"] = {**rows["D2"], "value_text": "35", "source_id": "S3"}
    workflow = build_workflow(*arguments)
    assert workflow["research_ir"]["conflicts"][0]["status"] == "unresolved"
    assert not any(visual["kind"] == "line" for visual in workflow["visuals"])
