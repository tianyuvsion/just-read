"""Deterministic research transformations; no arbitrary code execution or network."""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import json
import math
import re


def fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), default=str).encode()).hexdigest()


def chunks_for(source: dict) -> list[dict]:
    if isinstance(source.get("chunks"), list) and source["chunks"]:
        return [dict(chunk) for chunk in source["chunks"] if isinstance(chunk, dict) and isinstance(chunk.get("text"), str)]
    text = source.get("raw_content") or ""
    return [{"id": f"{source['id']}:c{i + 1}", "text": text[start:start + 1500], "page": None,
             "paragraph": i + 1, "start": start, "end": min(start + 1500, len(text))}
            for i, start in enumerate(range(0, len(text), 1350))]


def select_source(source: dict, question: str, limit=7500) -> dict:
    """Keep all chunks in the snapshot; select a bounded excerpt for the model."""
    result = deepcopy(source)
    result["chunks"] = chunks_for(result)
    tokens = set(re.findall(r"[a-z0-9_]+", question.lower()))
    for phrase in re.findall(r"[\u4e00-\u9fff]{2,}", question):
        tokens.update(phrase[index:index+2] for index in range(len(phrase)-1))
    ranked = sorted(enumerate(result["chunks"]), key=lambda item: (
        -sum(token in item[1]["text"].lower() for token in tokens), item[0]))
    selected, remaining = [], limit
    for _, chunk in ranked:
        if remaining <= 0:
            break
        text = chunk["text"][:remaining]
        selected.append({**chunk, "text": text})
        remaining -= len(text) + 2
    result["selected_chunk_ids"] = [chunk["id"] for chunk in selected]
    result["selected_ranges"] = [{"chunk_id": chunk["id"], "page": chunk.get("page"),
        "paragraph": chunk.get("paragraph"), "start": chunk.get("start"),
        "end": chunk["start"] + len(chunk["text"]) if isinstance(chunk.get("start"), int) else None,
        "characters": len(chunk["text"])} for chunk in selected]
    result["raw_content"] = "\n\n".join(chunk["text"] for chunk in selected)[:limit]
    result["hash"] = result.get("hash") or fingerprint(result["chunks"])
    result["kind"] = result.get("kind", "web")
    return result


def model_source(source: dict) -> dict:
    # Do not send every uploaded page on every model call.
    return {key: source.get(key) for key in ("id", "title", "url", "kind", "raw_content", "selected_chunk_ids")}


def locate_support(support: dict, sources: list[dict], eid: str, statement_kind="analysis") -> dict:
    source = next((item for item in sources if item["id"] == support.get("source_id")), None)
    quote = str(support.get("quote") or "")
    locator = {"page": None, "paragraph": None, "start": None, "end": None}
    found, exact, chunk_id = False, False, None
    if source and quote.strip():
        for chunk in chunks_for(source):
            offset = chunk["text"].find(quote)
            if offset >= 0 or " ".join(quote.split()) in " ".join(chunk["text"].split()):
                found, exact, chunk_id = True, offset >= 0, chunk["id"]
                start = chunk.get("start")
                locator = {"page": chunk.get("page"), "paragraph": chunk.get("paragraph"),
                    "start": start + offset if exact and isinstance(start, int) else start,
                    "end": start + offset + len(quote) if exact and isinstance(start, int) else chunk.get("end")}
                break
    return {"id": eid, "source_id": support.get("source_id", ""), "chunk_id": chunk_id,
            "quote": quote, "locator": locator, "verification": {"quote_match": found, "exact_span": exact},
            "statement_kind": statement_kind}


def public_source(source: dict, evidence: list[dict]) -> dict:
    """Publish cited excerpts, never the rest of an uploaded or retrieved source.

    The immutable internal snapshot retains the original source and all chunks.
    A share exposes only explicit metadata and quotes already bound to this report.
    """
    metadata = ("id", "title", "name", "url", "kind", "hash", "media_type", "revision", "createdAt",
                "size_bytes", "text_characters", "timestamp", "accessed_at", "content_kind",
                "retrieval_provider", "retrieval_engine")
    result = {key: source[key] for key in metadata if key in source and
              (source[key] is None or isinstance(source[key], (str, int, float, bool)))}
    chunks = chunks_for(source)
    selected_ids = [value for value in source.get("selected_chunk_ids", [chunk["id"] for chunk in chunks])
                    if isinstance(value, str)]
    selected_ranges = source.get("selected_ranges", [{"chunk_id": chunk["id"],
        "page": chunk.get("page"), "paragraph": chunk.get("paragraph"), "start": chunk.get("start"),
        "end": chunk.get("end"), "characters": len(chunk["text"])} for chunk in chunks])
    refs = [item for item in evidence if item.get("source_id") == source["id"] and item.get("quote", "").strip()]
    excerpts = [{"evidence_id": item["id"], "chunk_id": item.get("chunk_id"), "quote": item["quote"],
                 "locator": deepcopy(item["locator"]), "verification": deepcopy(item["verification"])} for item in refs]
    quoted_ids = list(dict.fromkeys(item["chunk_id"] for item in excerpts if item["chunk_id"]))
    result["chunks"] = [{**{key: chunk.get(key) for key in ("id", "page", "paragraph", "start", "end")},
                         "quotes": [deepcopy(item) for item in excerpts if item["chunk_id"] == chunk["id"]]}
                        for chunk in chunks]
    result["excerpts"] = excerpts
    result["selection"] = {"total_chunks": len(chunks), "selected_chunk_ids": list(selected_ids),
        "selected_ranges": [{key: item.get(key) for key in ("chunk_id", "page", "paragraph", "start", "end", "characters")}
                            for item in selected_ranges if isinstance(item, dict)],
        "selected_characters": len(source.get("raw_content") or ""),
        "quoted_chunk_ids": quoted_ids, "quoted_evidence_count": len(excerpts),
        "notice": (f"本次选入模型的材料片段为 {len(selected_ids)}/{len(chunks)} 个，"
                   f"本报告包含 {len(excerpts)} 条候选引用摘录。" if excerpts else
                   f"本次选入模型的材料片段为 {len(selected_ids)}/{len(chunks)} 个；本来源未被引用。") +
                  "选入不代表整份材料已经阅读或核实；公开报告仅含定位元数据和已引用摘录，不包含未引用正文。"}
    return result


NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d+)?|\.\d+|\d{1,3}(?:,\d{3})+(?:\.\d+)?)(?:[eE][+-]?\d+)?[%％]?")
PERCENT_UNITS = {"%", "％", "percent", "percentage", "百分比"}


def number(value, unit="") -> Decimal:
    text = str(value).strip()
    if not NUMBER.fullmatch(text):
        raise ValueError("ambiguous_number")
    if text.endswith(("%", "％")) and unit and unit.lower() not in PERCENT_UNITS:
        raise ValueError("unit_mismatch")
    result = Decimal(text.replace(",", "").rstrip("%％"))
    if not result.is_finite() or not math.isfinite(float(result)) or result != 0 and float(result) == 0:
        raise ValueError("nonfinite_number")
    return result


OPERATIONS = {"sum", "mean", "min", "max", "difference", "ratio", "percent_change"}


def calculate(spec: dict, data: dict, cid: str) -> dict:
    """Calculate only by references to extracted data; never eval expressions."""
    operation = spec.get("operation", "")
    result = {"id": cid, "operation": operation, "inputs": [], "formula": "", "result": None,
              "unit": "", "source_refs": [], "status": "unavailable"}
    refs = spec.get("input_refs") or spec.get("labels") or []
    try:
        if operation not in OPERATIONS or not isinstance(refs, list) or not refs:
            raise ValueError("unsupported_operation_or_inputs")
        for reference in refs:
            matches = [(key, item) for key, item in data.items() if key == reference or item.get("label") == reference]
            if len(matches) != 1:
                raise ValueError("unknown_or_ambiguous_input")
            key, item = matches[0]
            result["inputs"].append({"data_id": key, "label": item["label"], "value_text": item["value_text"],
                                     "unit": item.get("unit", ""), "source_id": item.get("source_id", "")})
        units = {item["unit"] for item in result["inputs"]}
        if len(units) != 1:
            raise ValueError("incompatible_units")
        values = [number(item["value_text"], item["unit"]) for item in result["inputs"]]
        if operation in {"difference", "ratio", "percent_change"} and len(values) != 2:
            raise ValueError("two_inputs_required")
        with localcontext() as context:
            context.prec = 40
            if operation == "sum": output = sum(values)
            elif operation == "mean": output = sum(values) / len(values)
            elif operation == "min": output = min(values)
            elif operation == "max": output = max(values)
            elif operation == "difference": output = values[1] - values[0]
            elif operation == "ratio":
                if not values[1]: raise ValueError("division_by_zero")
                output = values[0] / values[1]
            else:
                if not values[0]: raise ValueError("division_by_zero")
                output = (values[1] - values[0]) / abs(values[0]) * 100
        number(str(output))
        input_unit = next(iter(units))
        percentage_inputs = input_unit.lower() in PERCENT_UNITS or (
            not input_unit and all(str(item["value_text"]).strip().endswith(("%", "％")) for item in result["inputs"]))
        # Percent values are represented on their displayed 0–100 scale.
        # Their absolute difference is in percentage points, while relative
        # change remains a percentage. Keep each original input's unit intact.
        result_unit = ("%" if operation == "percent_change" else "ratio" if operation == "ratio" else
                       "百分点" if operation == "difference" and percentage_inputs else input_unit)
        result.update(result=float(output), result_decimal=str(output), status="computed",
                      unit=result_unit,
                      formula={"sum": "sum(inputs)", "mean": "sum(inputs)/count(inputs)", "min": "min(inputs)",
                               "max": "max(inputs)", "difference": "inputs[1]-inputs[0]", "ratio": "inputs[0]/inputs[1]",
                               "percent_change": "(inputs[1]-inputs[0])/abs(inputs[0])*100"}[operation],
                      source_refs=list(dict.fromkeys(item["source_id"] for item in result["inputs"] if item["source_id"])))
    except (ValueError, InvalidOperation, OverflowError, ArithmeticError) as exc:
        result["reason"] = str(exc) if isinstance(exc, ValueError) else "invalid_arithmetic"
    return result


def build_workflow(spec, sources, plan, findings, data, review, layout, chapters, calculations=None, relations=None):
    evidence, claims, datasets, conflicts = [], [], [], []
    for fid, finding in findings.items():
        refs = []
        kind = finding.get("statement_kind", "analysis")
        for support in finding.get("supports", []):
            item = locate_support(support, sources, f"E{len(evidence) + 1}", kind)
            evidence.append(item)
            refs.append(item["id"])
        claims.append({"id": fid, "text": finding["text"], "subquestion_id": finding.get("subquestion_id", ""),
                       "evidence_refs": refs, "statement_kind": kind,
                       "calculation_refs": finding.get("calculation_refs", [])})
    groups = {}
    for did, datum in data.items():
        unit = datum.get("unit", "") or ("%" if datum["value_text"].strip().endswith(("%", "％")) else "")
        key = (datum.get("metric", ""), unit, datum.get("period", ""))
        rows = groups.setdefault(key, [])
        try: value = float(number(datum["value_text"], datum.get("unit", "")))
        except (ValueError, InvalidOperation, OverflowError): value = None
        rows.append({"id": did, "label": datum.get("label", ""), "value": value, "value_text": datum["value_text"],
                     "source_id": datum.get("source_id", ""), "subquestion_id": datum.get("subquestion_id", "")})
    for (metric, unit, period), rows in groups.items():
        datasets.append({"id": f"DS{len(datasets) + 1}", "metric": metric, "unit": unit, "period": period, "rows": rows})
        by_label = {}
        for row in rows: by_label.setdefault(row["label"], []).append(row)
        for label, group in by_label.items():
            if len({row["value"] if row["value"] is not None else row["value_text"] for row in group}) > 1:
                conflicts.append({"id": f"X{len(conflicts) + 1}", "kind": "numeric_disagreement", "subject": label,
                                  "data_refs": [row["id"] for row in group], "status": "unresolved",
                                  "description": "相同指标、单位、时期存在不同数值，保留各来源，需人工判断。"})
    # Review disagreements are preserved as explicit unresolved items, not silently resolved.
    for decision in review.get("decisions", []):
        if not decision.get("supported", False):
            conflicts.append({"id": f"X{len(conflicts) + 1}", "kind": "review_disagreement",
                              "claim_refs": [decision.get("id")], "status": "unresolved"})
    questions = plan.get("subquestions", [])
    answers = [{"subquestion_id": q.get("id", f"Q{i+1}"), "question": q["question"],
                "claim_refs": [c["id"] for c in claims if c["subquestion_id"] == q.get("id", f"Q{i+1}")]}
               for i, q in enumerate(questions)]
    unknowns = [f"子问题尚无结论：{q['question']}" for q in answers if not q["claim_refs"]]
    claim_map = {claim["id"]: claim for claim in claims}
    blocks, reading_sections = [], []
    for index, section in enumerate(layout.get("sections", [])):
        chapter_id = "comparison" if index == 0 else f"section-{index+1}"
        refs = [fid for fid in section.get("finding_ids", []) if fid in claim_map]
        erefs = list(dict.fromkeys(e for fid in refs for e in claim_map[fid]["evidence_refs"]))
        reading_sections.append({"chapter_id": chapter_id, "question": section["title"], "goal": "理解本节结论与证据边界",
                                 "prerequisites": [], "evidence_refs": erefs, "visual_ids": []})
        for fid in refs:
            claim = claim_map[fid]
            blocks.append({"id": f"NB{len(blocks)+1}", "chapter_id": chapter_id,
                           "semantic_role": "calculation" if claim["calculation_refs"] else "finding",
                           "text": claim["text"], "claim_refs": [fid], "evidence_refs": claim["evidence_refs"]})
    visuals = []
    # This is a traceability graph of actual artifacts, not an invented domain process.
    nodes = [{"id": q["subquestion_id"], "label": q["question"]} for q in answers]
    nodes += [{"id": c["id"], "label": c["text"][:160]} for c in claims[:40]]
    allowed_nodes = {node["id"] for node in nodes}
    edges = [{"source": q["subquestion_id"], "target": fid, "type": "produces", "label": "研究结论",
              "evidence_refs": claim_map[fid]["evidence_refs"]} for q in answers for fid in q["claim_refs"] if fid in allowed_nodes]
    if nodes:
        visuals.append({"id": "V-flow", "kind": "flow", "title": "子问题与结论路径", "chapter_id": "comparison",
                        "nodes": nodes, "edges": edges, "provenance": {"kind": "workflow_trace"}})
    evidence_ids = {item["id"] for item in evidence}
    relation_edges, relation_nodes = [], {}
    for relation in relations or []:
        source, target = str(relation.get("source", "")), str(relation.get("target", ""))
        if not source or not target: continue
        for label in (source, target): relation_nodes[label] = {"id": "N"+fingerprint(label)[:10], "label": label}
        refs = [ref for ref in relation.get("evidence_refs", []) if ref in evidence_ids]
        for support in relation.get("supports", []):
            item = locate_support(support, sources, f"E{len(evidence)+1}", "relation")
            evidence.append(item)
            refs.append(item["id"])
        relation_edges.append({"source": relation_nodes[source]["id"], "target": relation_nodes[target]["id"],
                               "type": relation.get("type", "related_to"), "label": relation.get("label", "关联待核实"),
                               "evidence_refs": refs})
    if relation_edges:
        visuals.append({"id": "V-relation", "kind": "relation", "title": "候选关系图", "chapter_id": "comparison",
                        "nodes": list(relation_nodes.values()), "edges": relation_edges,
                        "provenance": {"kind": "model_analysis", "verified": False}})
    for dataset in datasets:
        rows = [row for row in dataset["rows"] if row["value"] is not None]
        if not rows: continue
        visuals.append({"id": "V-"+dataset["id"], "kind": "bar", "title": dataset["metric"] or "候选数据",
                        "chapter_id": "comparison", "labels": [row["label"] for row in rows],
                        "series": [{"name": dataset["metric"] or "数值", "values": [row["value"] for row in rows]}],
                        "unit": dataset["unit"], "period": dataset["period"],
                        "provenance": {"dataset_id": dataset["id"], "verified": False}})
        visuals.append({**deepcopy(visuals[-1]), "id": "T-"+dataset["id"], "kind": "table",
                        "title": (dataset["metric"] or "候选数据") + "数据表"})
    timelines = {}
    for dataset in datasets:
        period = dataset["period"]
        if not re.fullmatch(r"\d{4}(?:年|(?:-\d{2}){0,2})", period): continue
        for row in dataset["rows"]:
            if row["value"] is not None:
                timelines.setdefault((dataset["metric"], dataset["unit"], row["label"]), []).append(
                    (period, row["value"], dataset["id"]))
    for (metric, unit, label), items in timelines.items():
        if len({period for period, _, _ in items}) < 2: continue
        # Conflicting values for one period remain in datasets; a line would imply a false single trajectory.
        if len({period for period, _, _ in items}) != len(items): continue
        items.sort(key=lambda item: item[0])
        visuals.append({"id": "L-"+fingerprint([metric, unit, label])[:10], "kind": "line",
                        "title": f"{label}：{metric}", "chapter_id": "comparison", "labels": [item[0] for item in items],
                        "series": [{"name": label, "values": [item[1] for item in items]}], "unit": unit,
                        "period": f"{items[0][0]}—{items[-1][0]}",
                        "provenance": {"dataset_ids": [item[2] for item in items], "verified": False}})
    for section in reading_sections:
        section["visual_ids"] = [visual["id"] for visual in visuals if visual["chapter_id"] == section["chapter_id"]]
    public_sources = [public_source(source, evidence) for source in sources]
    return {"schema_version": "1.0", "research_spec": spec, "sources": public_sources,
            "evidence": evidence, "plan": plan,
            "research_ir": {"question_answers": answers, "claims": claims,
                "coverage": [{"subquestion_id": answer["subquestion_id"], "claims": len(answer["claim_refs"]),
                              "status": "answered" if answer["claim_refs"] else "gap"} for answer in answers],
                "conflicts": conflicts, "unknowns": unknowns, "relations": relation_edges},
            "datasets": datasets, "calculations": calculations or [],
            "reading_plan": {"audience": spec.get("reader", "普通读者"), "goal": spec.get("question", ""),
                             "chapter_order": [chapter["id"] for chapter in chapters], "sections": reading_sections},
            "narrative_blocks": blocks, "visuals": visuals}


def normalize_scene(value: dict, evidence: list[dict]) -> dict:
    valid_refs = {item["id"] for item in evidence if item.get("verification", {}).get("quote_match")}
    parts, unknowns, id_map = [], list(value.get("unknowns") or []), {}
    for index, part in enumerate(value.get("parts") or []):
        refs = [ref for ref in part.get("evidence_refs", []) if ref in valid_refs]
        if not refs or part.get("geometry") not in {"box", "sphere", "cylinder"}:
            unknowns.append("有部件缺少可定位依据或有效几何规格，未构建。")
            continue
        try:
            position = [float(v) for v in part.get("position", [0, 0, 0])]
            size = [float(v) for v in part.get("size", [1, 1, 1])]
            if len(position) != 3 or len(size) != 3 or not all(math.isfinite(v) for v in position + size):
                raise ValueError
        except (TypeError, ValueError):
            unknowns.append("有部件坐标无法表示，未构建。")
            continue
        id_map[part.get("id") or f"P{index+1}"] = f"P{index+1}"
        parts.append({"id": f"P{index+1}", "label": str(part.get("label") or f"部件{index+1}")[:160],
                      "geometry": part["geometry"], "position": [max(-10, min(10, v)) for v in position],
                      "size": [max(.1, min(10, abs(v))) for v in size],
                      "color": part.get("color") if re.fullmatch(r"#[0-9a-fA-F]{6}", str(part.get("color"))) else "#6c8cff",
                      "description": str(part.get("description") or "概念结构示意；不代表实测尺寸。"),
                      "evidence_refs": refs, "dimensions_known": False})
    if not parts: unknowns.append("未取得足够结构依据，未生成三维部件。")
    relations = [{"source": id_map[item["source"]], "target": id_map[item["target"]], "type": item.get("type", "related_to")}
                 for item in value.get("relations", []) if item.get("source") in id_map and item.get("target") in id_map]
    return {"id": "scene-1", "title": str(value.get("title") or "概念结构示意"), "kind": "schematic",
            "parts": parts, "relations": relations, "annotations": ["归一化概念布局；位置、比例与颜色仅用于表达，不是物理测量。"],
            "unknowns": list(dict.fromkeys(unknowns)), "provenance": {"mode": "conceptual", "dimensions_known": False}}
