"""Version-bound publication checks and a self-contained, offline report reader.

Checks describe limitations; they are not research-quality admission gates. The
package manifest hashes files, while the report manifest hashes a projection
without the manifest itself, so neither checksum is self-referential.
"""
from __future__ import annotations

from copy import deepcopy
import base64
import hashlib
import html
import io
import json
import math
import re
import struct
from zipfile import ZipFile, ZIP_DEFLATED


def json_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value) -> str:
    return hashlib.sha256(json_bytes(value)).hexdigest()


def report_body(report: dict) -> dict:
    body = deepcopy(report)
    # Bookmarks are personal reader state, independent of immutable report text.
    body.pop("bookmarks", None)
    if isinstance(body.get("workflow"), dict):
        body["workflow"].pop("manifest", None)
    return body


def _safe_json(value):
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, list):
        return [_safe_json(item) for item in value]
    if isinstance(value, dict):
        return {key: _safe_json(item) for key, item in value.items()}
    return value


def public_report(report: dict) -> dict:
    """Freeze a report-only projection, including cited quotes but no full sources."""
    value = deepcopy(report)
    private_keys = {"raw_content", "pages", "blob", "content_base64", "api_key", "llm_api_key",
                    "search_api_key", "authorization", "cookie", "set-cookie", "headers", "prompt",
                    "access_token", "refresh_token", "_evidence"}
    chunk_fields = {"id", "page", "paragraph", "start", "end", "quotes"}
    quote_fields = {"evidence_id", "chunk_id", "quote", "locator", "verification", "source_id"}
    source_fields = {"id", "title", "name", "url", "kind", "hash", "media_type", "revision", "createdAt",
                     "size_bytes", "text_characters", "timestamp", "accessed_at", "content_kind",
                     "retrieval_provider", "retrieval_engine", "chunks", "excerpts", "selection"}
    original_workflow = value.get("workflow") or {}
    evidence = original_workflow.get("evidence", [])
    if isinstance(evidence, list):
        sources = original_workflow.get("sources")
        for source in sources if isinstance(sources, list) else []:
            if not isinstance(source, dict):
                continue
            excerpts = [{"evidence_id": item.get("id"), "chunk_id": item.get("chunk_id"), "quote": item["quote"],
                         "locator": item.get("locator", {}), "verification": item.get("verification", {})}
                        for item in evidence if isinstance(item, dict) and item.get("source_id") == source.get("id") and isinstance(item.get("quote"), str)]
            if not source.get("excerpts"):
                source["excerpts"] = excerpts
            chunks = source.get("chunks")
            for chunk in chunks if isinstance(chunks, list) else []:
                if isinstance(chunk, dict) and not chunk.get("quotes"):
                    chunk["quotes"] = [quote for quote in excerpts if quote["chunk_id"] == chunk.get("id")]

    def clean(item):
        if isinstance(item, list):
            return [clean(child) for child in item]
        if not isinstance(item, dict):
            return item
        result = {}
        for key, child in item.items():
            if key.lower() in private_keys:
                continue
            if key == "chunks" and isinstance(child, list):
                result[key] = [clean({k: v for k, v in chunk.items() if k in chunk_fields}) for chunk in child if isinstance(chunk, dict)]
            elif key in {"excerpts", "quotes"} and isinstance(child, list):
                result[key] = [clean({k: v for k, v in quote.items() if k in quote_fields}) for quote in child if isinstance(quote, dict)]
            elif key == "sources" and isinstance(child, list):
                result[key] = [clean({k: v for k, v in source.items() if k in source_fields}) for source in child if isinstance(source, dict)]
            else:
                result[key] = clean(child)
        return result

    value = clean(value)
    value["bookmarks"] = []
    workflow = value.setdefault("workflow", {})
    if workflow is None:
        workflow = value["workflow"] = {}
    original = (report.get("workflow") or {}).get("manifest") or {}
    manifest = deepcopy(original) if isinstance(original, dict) else {}
    manifest.update(schema="justread.report-manifest.v1", report_id=value["id"], version=int(value.get("version", 1)),
                    hash_algorithm="sha256", delivery_scope="public", origin_body_sha256=digest(report_body(report)),
                    body_projection="public report without bookmarks and workflow.manifest")
    workflow["manifest"] = manifest
    manifest["body_sha256"] = digest(report_body(value))
    manifest["asset_hashes"] = {name: digest(workflow[name]) for name in ("visuals", "scene", "research_ir", "reading_plan", "ai_quality_review", "auto_revision") if name in workflow}
    return value


def prepare_report(report: dict, evidence: dict | None, request: dict | None = None) -> tuple[dict, dict]:
    """Return repaired copies with automatic D-stage review and version hashes."""
    report, evidence = _safe_json(deepcopy(report)), _safe_json(deepcopy(evidence or {}))
    report["version"] = int(report.get("version") or 1)
    previous = (report.get("workflow") or {}).get("manifest", {})
    previous = previous if isinstance(previous, dict) else {}
    if (previous.get("report_id") == report.get("id") and previous.get("version") == report["version"]
            and previous.get("body_sha256") == digest(report_body(report))
            and previous.get("evidence_sha256") == digest(evidence)
            and evidence.get("publication", {}).get("request_hash") == digest(request or {})):
        return report, evidence
    workflow = deepcopy(report.get("workflow") or {})
    warnings = list(evidence.get("validation", {}).get("warnings", []))
    repairs, used_ids, chapters = [], set(), []
    for name in ("sources", "evidence", "visuals", "narrative_blocks", "datasets", "calculations"):
        if name not in workflow:
            continue
        original = workflow[name]
        if not isinstance(original, list) or any(not isinstance(item, dict) for item in original):
            evidence.setdefault("unparsed_workflow", {})[name] = original
            workflow[name] = [item for item in original if isinstance(item, dict)] if isinstance(original, list) else []
            repairs.append(f"{name} 的无效结构保存在内部记录，展示使用可读取条目。")
    for name in ("scene", "reading_plan", "research_ir", "ai_quality_review", "auto_revision"):
        if name in workflow and not isinstance(workflow[name], dict):
            evidence.setdefault("unparsed_workflow", {})[name] = workflow[name]
            workflow[name] = {}
            repairs.append(f"{name} 结构不可读取，原值保存在内部记录。")
    for index, item in enumerate(report.get("chapters") or []):
        chapter = dict(item)
        candidate = str(chapter.get("id") or f"chapter-{index + 1}")
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", candidate) or candidate in used_ids:
            candidate = f"chapter-{index + 1}"
            while candidate in used_ids:
                candidate += "-copy"
            repairs.append("已修复重复或无效的章节编号。")
        used_ids.add(candidate)
        chapter.update(id=candidate, title=str(chapter.get("title") or "研究内容")[:300],
                       paragraphs=[str(p) for p in chapter.get("paragraphs", []) if p is not None])
        chapters.append(chapter)
    if not chapters:
        chapters = [{"id": "overview", "title": "调研概览", "paragraphs": [str(report.get("summary") or "暂无正文，请查看生成说明。")]}]
        used_ids.add("overview")
        repairs.append("已补充可阅读的概览章节。")
    report["chapters"] = chapters
    report["bookmarks"] = list(dict.fromkeys(b for b in report.get("bookmarks", []) if b in used_ids))
    points = [p for p in report.get("chart", []) if isinstance(p, dict) and isinstance(p.get("value"), (int, float))
              and not isinstance(p["value"], bool) and math.isfinite(p["value"])]
    if len(points) != len(report.get("chart", [])):
        repairs.append("不可绘制的图表值已移除；原始研究数据保留在证据记录中。")
    report["chart"] = points
    if points and not report.get("chartMeta"):
        report["chartMeta"] = {"title": "候选数据", "unit": "未标注单位", "note": "数据与统计口径待核实。"}
        repairs.append("图表缺少口径说明，已标注为待核实。")
    sources = evidence.get("sources", [])
    source_ids = {str(s.get("id")) for s in sources if isinstance(s, dict) and s.get("id")}
    cited = set(re.findall(r"\[(S\d+)\]", "\n".join(p for c in chapters for p in c["paragraphs"])))
    missing = sorted(cited - source_ids) if sources else sorted(cited)
    if missing:
        warnings.append("部分正文引用未绑定到本版本证据记录：" + "、".join(missing[:30]))
    if not sources and report.get("source") == "generated":
        warnings.append("本版本没有外部来源记录，正文事实及数字需要人工核实。")
    evidence_ids = {str(item.get("id")) for item in workflow.get("evidence", []) if item.get("id")}
    visuals, scene = workflow.get("visuals", []), workflow.get("scene") or {}
    binding_warnings = []
    for visual in visuals:
        if visual.get("chapter_id") and str(visual["chapter_id"]) not in used_ids:
            binding_warnings.append(f"图示 {visual.get('id', '')} 关联的章节不存在。")
        nodes = visual.get("nodes") if isinstance(visual.get("nodes"), list) else []
        node_ids = {str(n.get("id")) for n in nodes if isinstance(n, dict)}
        for edge in visual.get("edges", []) if isinstance(visual.get("edges"), list) else []:
            if not isinstance(edge, dict):
                continue
            if str(edge.get("source", edge.get("from"))) not in node_ids or str(edge.get("target", edge.get("to"))) not in node_ids:
                binding_warnings.append(f"图示 {visual.get('id', '')} 有未绑定节点的关系。")
            refs = edge.get("evidence_refs")
            if any(str(ref) not in evidence_ids for ref in (refs if isinstance(refs, list) else [])):
                binding_warnings.append(f"图示 {visual.get('id', '')} 有未绑定证据的关系。")
    parts = scene.get("parts") if isinstance(scene.get("parts"), list) else []
    for part in parts:
        if not isinstance(part, dict):
            continue
        refs = part.get("evidence_refs")
        if any(str(ref) not in evidence_ids for ref in (refs if isinstance(refs, list) else [])):
            binding_warnings.append(f"三维部件 {part.get('id', '')} 的部分证据编号不属于当前版本。")
    order = (workflow.get("reading_plan") or {}).get("chapter_order", [])
    if isinstance(order, list) and any(str(chapter_id) not in used_ids for chapter_id in order):
        binding_warnings.append("阅读顺序包含不存在的章节，需要重新编排。")
    warnings = list(dict.fromkeys(str(w) for w in warnings + repairs + binding_warnings))
    checks = [
        {"id": "readable_content", "status": "pass" if any(c["paragraphs"] for c in chapters) else "warning", "message": "已检查可阅读章节与正文。"},
        {"id": "chapter_references", "status": "pass", "message": "章节编号和书签引用保持一致。"},
        {"id": "source_references", "status": "warning" if missing or not sources else "pass", "message": "检查引用编号绑定；不代表来源真实性或结论已核实。"},
        {"id": "finite_chart", "status": "pass", "message": "交付图表只包含有限数值；候选数据的事实与口径仍需核实。"},
        {"id": "asset_bindings", "status": "warning" if binding_warnings else "pass", "message": "已检查图示、三维部件、章节及证据编号绑定；未核实项仅提示。"},
    ]
    workflow["quality_review"] = {"kind": "automatic", "status": "warnings" if warnings else "checked",
        "checks": checks, "warnings": warnings, "repairs": repairs, "repair_count": len(repairs),
        "human_status": "not_reviewed", "report_version": report["version"],
        "after_expression_revision": bool(workflow.get("auto_revision", {}).get("rounds"))}
    report["workflow"] = workflow
    # Evidence retains machine checks separately from any model semantic review.
    evidence["quality_review"] = deepcopy(workflow["quality_review"])
    evidence["publication"] = {"report_id": report.get("id"), "version": report["version"],
        "request_hash": digest(request or {}), "mode": "warnings_only"}
    workflow["manifest"] = {"schema": "justread.report-manifest.v1", "report_id": report.get("id"),
        "version": report["version"], "hash_algorithm": "sha256", "body_sha256": digest(report_body(report)),
        "evidence_sha256": digest(evidence), "body_projection": "report without bookmarks and workflow.manifest",
        "asset_hashes": {name: digest(workflow[name]) for name in ("visuals", "scene", "research_ir", "reading_plan", "ai_quality_review", "auto_revision") if name in workflow}}
    return report, evidence


def version_diff(before: dict, after: dict) -> dict:
    """Concise, deterministic metadata rather than duplicating report bodies."""
    old = {c["id"]: c for c in before.get("chapters", [])}
    new = {c["id"]: c for c in after.get("chapters", [])}
    return {"fields": [k for k in ("title", "question", "summary", "category", "chart", "chartMeta") if before.get(k) != after.get(k)],
        "chapters_added": sorted(new.keys() - old.keys()), "chapters_removed": sorted(old.keys() - new.keys()),
        "chapters_changed": sorted(k for k in old.keys() & new.keys() if old[k] != new[k])}


def scene_glb(scene: dict | None) -> bytes | None:
    """Generate glTF 2.0 meshes from explicitly schematic, local primitives."""
    if not scene or not scene.get("parts"):
        return None
    raw = bytearray()
    gltf = {"asset": {"version": "2.0", "generator": "JustREAD local schematic mesh exporter"},
            "scene": 0, "scenes": [{"nodes": []}], "nodes": [], "meshes": [], "materials": [],
            "buffers": [], "bufferViews": [], "accessors": [], "extras": {
                "kind": "schematic", "notice": "Parameterized schematic, not a measured physical model.",
                "unknowns": scene.get("unknowns", []), "relations": scene.get("relations", [])}}

    def accessor(values, width, component, target):
        while len(raw) % 4:
            raw.append(0)
        offset = len(raw)
        flattened = [v for row in values for v in row] if width > 1 else values
        packed = struct.pack("<" + ("f" if component == 5126 else "H") * len(flattened), *flattened)
        raw.extend(packed)
        view = len(gltf["bufferViews"])
        gltf["bufferViews"].append({"buffer": 0, "byteOffset": offset, "byteLength": len(packed), "target": target})
        value = {"bufferView": view, "componentType": component, "count": len(values), "type": "VEC3" if width == 3 else "SCALAR"}
        if width == 3:
            value.update(min=[min(v[i] for v in values) for i in range(3)], max=[max(v[i] for v in values) for i in range(3)])
        gltf["accessors"].append(value)
        return len(gltf["accessors"]) - 1

    def geometry(kind):
        if kind == "box":
            vertices = [[x, y, z] for x in (-.5, .5) for y in (-.5, .5) for z in (-.5, .5)]
            indices = [0, 1, 3, 0, 3, 2, 4, 6, 7, 4, 7, 5, 0, 4, 5, 0, 5, 1, 2, 3, 7, 2, 7, 6, 0, 2, 6, 0, 6, 4, 1, 5, 7, 1, 7, 3]
        elif kind == "cylinder":
            count = 24
            vertices = [[0, -.5, 0], [0, .5, 0]]
            vertices += [[.5 * math.cos(i * math.tau / count), y, .5 * math.sin(i * math.tau / count)] for y in (-.5, .5) for i in range(count)]
            indices = []
            for i in range(count):
                a, b, c, d = 2+i, 2+(i+1)%count, 2+count+i, 2+count+(i+1)%count
                indices += [0, b, a, 1, c, d, a, b, d, a, d, c]
        else:
            rows, columns = 12, 24
            vertices = [[.5*math.sin(i*math.pi/rows)*math.cos(j*math.tau/columns), .5*math.cos(i*math.pi/rows), .5*math.sin(i*math.pi/rows)*math.sin(j*math.tau/columns)] for i in range(rows+1) for j in range(columns+1)]
            indices = []
            for i in range(rows):
                for j in range(columns):
                    a = i*(columns+1)+j
                    indices += [a, a+1, a+columns+2, a, a+columns+2, a+columns+1]
        normals = [[0., 0., 0.] for _ in vertices]
        for i in range(0, len(indices), 3):
            a, b, c = [vertices[indices[i+j]] for j in range(3)]
            u, v = [b[j]-a[j] for j in range(3)], [c[j]-a[j] for j in range(3)]
            n = [u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0]]
            for j in range(3):
                normals[indices[i+j]] = [normals[indices[i+j]][k]+n[k] for k in range(3)]
        normals = [[x/(math.sqrt(sum(v*v for v in n)) or 1) for x in n] for n in normals]
        return {"POSITION": accessor(vertices, 3, 5126, 34962), "NORMAL": accessor(normals, 3, 5126, 34962)}, accessor(indices, 1, 5123, 34963)

    primitives = {}
    for part in scene["parts"]:
        if not isinstance(part, dict):
            continue
        kind = part.get("geometry", "box")
        if kind not in {"box", "sphere", "cylinder"}:
            continue
        try:
            position = [float(v) for v in part.get("position", [0, 0, 0])]
            size = [float(v) for v in part.get("size", [1, 1, 1])]
            if len(position) != 3 or len(size) != 3 or not all(math.isfinite(v) and abs(v) < 1e20 for v in position+size) or not all(v > 0 for v in size):
                continue
        except (ValueError, TypeError, OverflowError):
            continue
        if kind not in primitives:
            primitives[kind] = geometry(kind)
        attributes, indices = primitives[kind]
        color = str(part.get("color") or "#69a7ef")
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            color = "#69a7ef"
        rgba = [int(color[i:i+2], 16)/255 for i in (1, 3, 5)] + [1]
        material_index = len(gltf["materials"])
        gltf["materials"].append({"pbrMetallicRoughness": {"baseColorFactor": rgba, "metallicFactor": 0, "roughnessFactor": .8}, "doubleSided": True})
        mesh_index = len(gltf["meshes"])
        gltf["meshes"].append({"primitives": [{"attributes": attributes, "indices": indices, "material": material_index}]})
        gltf["scenes"][0]["nodes"].append(len(gltf["nodes"]))
        gltf["nodes"].append({"name": str(part.get("label") or part.get("id") or kind), "mesh": mesh_index,
            "translation": position, "scale": size, "extras": {"id": part.get("id"), "geometry": kind,
                "description": part.get("description", ""), "evidence_refs": part.get("evidence_refs", []),
                "dimensions_known": bool(part.get("dimensions_known", False)), "kind": "schematic"}})
    if not gltf["nodes"]:
        return None
    while len(raw) % 4:
        raw.append(0)
    gltf["buffers"] = [{"byteLength": len(raw)}]
    metadata = json_bytes(gltf)
    metadata += b" " * (-len(metadata) % 4)
    length = 12 + 8 + len(metadata) + 8 + len(raw)
    return struct.pack("<III", 0x46546C67, 2, length) + struct.pack("<II", len(metadata), 0x4E4F534A) + metadata + struct.pack("<II", len(raw), 0x004E4942) + raw


STYLE = """
:root{color-scheme:light;font:16px/1.7 system-ui,sans-serif;color:#162538;background:#f4f7fc}
*{box-sizing:border-box}body{margin:0}header{padding:36px max(5vw,20px);background:#152d4a;color:white}
h1{font-size:clamp(26px,4vw,42px);line-height:1.25}h2{line-height:1.35}small,.muted{color:#64748b}
header .muted{color:#cad6e5}main{display:grid;grid-template-columns:240px minmax(0,1fr);gap:28px;max-width:1360px;margin:28px auto;padding:0 20px}
aside{position:sticky;top:20px;align-self:start}aside a{display:block;padding:6px 0;color:#255892;text-decoration:none}
input,button,select{font:inherit;border:1px solid #cbd5e1;border-radius:7px;padding:8px 10px;background:white;color:#17314f}
button{cursor:pointer}input{width:100%}article section,.card{background:white;padding:24px;border:1px solid #dfe7f2;border-radius:14px;margin-bottom:20px;overflow-wrap:anywhere}
p{white-space:pre-wrap}pre{white-space:pre-wrap;max-height:420px;overflow:auto;font-size:13px}mark{background:#ffe58f}section[hidden]{display:none}
.toolbar{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}.warning{background:#fff6de;padding:12px;border-radius:8px}
svg{width:100%;height:auto;max-height:500px;touch-action:manipulation}svg text{font-family:system-ui,sans-serif}
.data-table{border-collapse:collapse;width:100%}.data-table td,.data-table th{padding:7px;border-bottom:1px solid #e4eaf3;text-align:left}
.scene{height:380px;width:100%;background:#101f35;border-radius:9px;cursor:grab;touch-action:none}.row{display:flex;gap:12px;flex-wrap:wrap}
.scene-wrap{position:relative;height:380px}.scene-labels{position:absolute;inset:0;width:100%;height:100%;pointer-events:none}.scene-tools label{font-size:13px}.scene-tools input{width:130px}.scene-tools input[type=checkbox]{width:auto}
.tag{display:inline-block;background:#e7effb;color:#254c80;border-radius:5px;padding:2px 8px;font-size:13px}.stat{font-size:13px}
@media(max-width:760px){main{display:block}aside{position:static;margin-bottom:20px}article section,.card{padding:16px}}
@media print{aside,.toolbar,input,button{display:none}main{display:block}header{color:#17253c;background:white}section{break-inside:avoid}}
"""


READER_SCRIPT = r"""
(()=>{'use strict';
const bundle=JSON.parse(document.getElementById('justread-data').textContent),r=bundle.report,e=bundle.evidence;
const $=s=>document.querySelector(s),el=(tag,text)=>{const x=document.createElement(tag);if(text!==undefined)x.textContent=text;return x};
const nav=$('#navigation'),article=$('#report-content');
(r.chapters||[]).forEach(c=>{let a=el('a',c.title);a.href='#'+c.id;nav.append(a);let s=el('section');s.id=c.id;s.append(el('h2',c.title));(c.paragraphs||[]).forEach(p=>s.append(el('p',p)));article.append(s)});
$('#search').addEventListener('input',event=>{const q=event.target.value.toLocaleLowerCase();let count=0;article.querySelectorAll('section').forEach(s=>{s.hidden=!!q&&!s.textContent.toLocaleLowerCase().includes(q);if(!s.hidden)count++});$('#search-status').textContent=q?count+' 个章节匹配':'显示全部章节'});
$('#evidence').textContent=JSON.stringify(e,null,2);$('#manifest').textContent=JSON.stringify(bundle.manifest,null,2);
const qr=r.workflow?.quality_review||{};$('#review').textContent=(qr.kind==='automatic'?'自动一致性检查':'检查记录')+' · '+(qr.status||'未执行')+' · 生成时人工审查：'+(qr.human_status||'not_reviewed');$('#review-history').textContent=(bundle.reviews||[]).map(v=>(v.kind==='human'?'人工':v.kind==='ai'?'AI':'自动')+' · '+(v.reviewer||'未署名')+' · '+(v.decision||v.status||'已记录')+' · '+(v.createdAt||'')+'\n'+(v.notes||'')).join('\n\n')||'导出时没有独立审查记录。';
$('#warnings').textContent=(qr.warnings||[]).join('\n')||'暂无自动检查提示；不代表事实已经人工核实。';
function svgEl(tag,attrs){const x=document.createElementNS('http://www.w3.org/2000/svg',tag);Object.entries(attrs||{}).forEach(([k,v])=>x.setAttribute(k,v));return x}
const visual=r.workflow?.visualizations||r.workflow?.expression||{},visuals=r.workflow?.visuals||[];
function graph(target,model,title){const box=$(target);let nodes=model?.nodes||[],edges=model?.edges||[];if(!nodes.length){box.append(el('p','当前版本没有'+title+'数据。'));return}const w=960,h=Math.max(250,Math.ceil(nodes.length/4)*135);let svg=svgEl('svg',{viewBox:`0 0 ${w} ${h}`,role:'img','aria-label':title});const map=new Map(nodes.map((n,i)=>[String(n.id??i),{x:125+(i%4)*235,y:60+Math.floor(i/4)*135,n}]));edges.forEach(edge=>{const a=map.get(String(edge.from??edge.source)),b=map.get(String(edge.to??edge.target));if(a&&b)svg.append(svgEl('line',{x1:a.x,y1:a.y,x2:b.x,y2:b.y,stroke:'#a0b9d9','stroke-width':2}))});map.forEach(({x,y,n})=>{const g=svgEl('g',{tabindex:0,role:'button','aria-label':n.label||n.title||n.id});g.append(svgEl('rect',{x:x-100,y:y-25,width:200,height:58,rx:10,fill:'#e9f1fe',stroke:'#7ea7e2'}));let t=svgEl('text',{x,y:y+6,'text-anchor':'middle','font-size':15,fill:'#1e477c'});t.textContent=String(n.label||n.title||n.id).slice(0,18);g.append(t);const select=()=>{$('#visual-detail').textContent=JSON.stringify(n,null,2);g.querySelector('rect').setAttribute('fill','#cce2ff')};g.addEventListener('click',select);g.addEventListener('keydown',ev=>{if(ev.key==='Enter')select()});svg.append(g)});box.append(svg)}
const flows=visuals.filter(v=>v.kind==='flow'),relations=visuals.filter(v=>v.kind==='relation');if(!flows.length)flows.push(visual.flow||visual.process||r.workflow?.flow);if(!relations.length)relations.push(visual.relations||visual.relationship||r.workflow?.relations);flows.forEach(v=>{if(v?.title)$('#flow').append(el('h4',v.title));graph('#flow',v,'流程图')});relations.forEach(v=>{if(v?.title)$('#relations').append(el('h4',v.title));graph('#relations',v,'关系图')});
const charts=Array.isArray(visual.charts)?visual.charts.slice():[];visuals.filter(v=>['bar','line','table'].includes(v.kind)).forEach(v=>(v.series||[]).forEach(s=>charts.push({title:v.title+' · '+s.name,unit:v.unit,kind:v.kind,note:v.provenance||'候选数据，待核实',data:(v.labels||[]).map((label,i)=>({label,value:s.values?.[i]}))})));if((r.chart||[]).length)charts.unshift({title:r.chartMeta?.title,unit:r.chartMeta?.unit,note:r.chartMeta?.note,data:r.chart});
charts.forEach(c=>{const card=el('div');card.append(el('h3',c.title||'数据图'),el('p',(typeof c.note==='string'?c.note:JSON.stringify(c.note||'候选数据，待核实'))+' · 单位：'+(c.unit||'未标注')));const data=c.data||c.points||[],finite=data.filter(d=>Number.isFinite(d.value));let table=el('table');table.className='data-table';let head=el('tr');head.append(el('th','项目'),el('th',c.unit||'数值'));table.append(head);const scale=Math.max(1,...finite.map(d=>Math.abs(d.value)));let svg=svgEl('svg',{viewBox:`0 0 900 ${c.kind==='line'?300:Math.max(70,finite.length*42)}`,role:'img','aria-label':c.title||'数据图'});const coords=[];finite.forEach((d,i)=>{let tr=el('tr');tr.append(el('td',d.label),el('td',String(d.value)));table.append(tr);if(c.kind==='line'){const x=70+i*760/Math.max(1,finite.length-1),y=145-d.value/scale*110;coords.push([x,y]);let dot=svgEl('circle',{cx:x,cy:y,r:5,fill:'#367ad8'});let title=svgEl('title');title.textContent=d.label+': '+d.value;dot.append(title);svg.append(dot);let label=svgEl('text',{x,y:280,'font-size':12,'text-anchor':'middle'});label.textContent=String(d.label).slice(0,12);svg.append(label)}else if(c.kind!=='table'){let text=svgEl('text',{x:10,y:i*42+25,'font-size':14});text.textContent=String(d.label).slice(0,20);svg.append(text);svg.append(svgEl('rect',{x:d.value<0?520-Math.abs(d.value/scale)*250:520,y:i*42+6,width:Math.abs(d.value/scale)*250,height:25,fill:d.value<0?'#ee9260':'#367ad8',rx:3}));let value=svgEl('text',{x:800,y:i*42+25,'font-size':13});value.textContent=d.value;svg.append(value)}});if(c.kind==='line'){svg.prepend(svgEl('polyline',{points:coords.map(p=>p.join(',')).join(' '),fill:'none',stroke:'#367ad8','stroke-width':3}));svg.prepend(svgEl('line',{x1:50,y1:145,x2:850,y2:145,stroke:'#cbd5e1'}))}if(c.kind!=='table')card.append(svg);card.append(table);$('#charts').append(card)});
if(!charts.length)$('#charts').append(el('p','当前版本没有数值图表。'));
__SCENE_RENDERER__
function download(name,data){let a=el('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000)}
$('#download-report').onclick=()=>download('report.json',r);$('#download-evidence').onclick=()=>download('evidence.json',e);
// Canonical key order matches the publisher's JSON representation.
function canonical(v){if(Array.isArray(v))return '['+v.map(canonical).join(',')+']';if(v&&typeof v==='object')return '{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+canonical(v[k])).join(',')+'}';return JSON.stringify(v)}
$('#verify').onclick=async()=>{try{if(!crypto.subtle)throw Error('此浏览器未提供离线 WebCrypto；请使用 ZIP 内 verify.py 校验。');let b=structuredClone(r);delete b.bookmarks;if(b.workflow)delete b.workflow.manifest;const hash=async text=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(text)))).map(n=>n.toString(16).padStart(2,'0')).join('');const ok=canonical(b)===canonical(JSON.parse(bundle.body_json))&&canonical(e)===canonical(JSON.parse(bundle.evidence_json))&&(await hash(bundle.body_json))===bundle.manifest.body_sha256&&(await hash(bundle.evidence_json))===bundle.manifest.evidence_sha256;$('#integrity').textContent=ok?'报告正文与证据哈希匹配；文件级校验请运行 ZIP 内 verify.py。':'校验不匹配：请重新取得完整版本。'}catch(err){$('#integrity').textContent=err.message}};
})();
"""


SCENE_RENDERER = r"""
(()=>{const canvas=$('#scene3d'),overlay=$('#scene-labels'),labels=overlay.getContext('2d');
if(!bundle.scene_glb){$('#scene-status').textContent='本版本没有可用的三维部件；不会补造模型。';return}
try{const raw=Uint8Array.from(atob(bundle.scene_glb),c=>c.charCodeAt(0)),view=new DataView(raw.buffer),jsonLength=view.getUint32(12,true),gltf=JSON.parse(new TextDecoder().decode(raw.slice(20,20+jsonLength))),binOffset=20+jsonLength+8;
const nodes=gltf.nodes||[],gl=canvas.getContext('webgl',{antialias:true,alpha:false});if(!gl)throw Error('此浏览器未启用 WebGL；仍可下载同包 scene.glb 在三维软件中查看。');canvas.dataset.renderer='webgl-mesh';
function shader(type,source){const s=gl.createShader(type);gl.shaderSource(s,source);gl.compileShader(s);if(!gl.getShaderParameter(s,gl.COMPILE_STATUS))throw Error('三维着色器不可用');return s}
const program=gl.createProgram();gl.attachShader(program,shader(gl.VERTEX_SHADER,'attribute vec3 aPosition;attribute vec3 aNormal;uniform mat3 uRotation;uniform mat4 uProjection;uniform vec3 uScale;uniform vec3 uPosition;uniform float uSceneScale;uniform float uDistance;varying vec3 vNormal;void main(){vec3 p=uRotation*((aPosition*uScale+uPosition)*uSceneScale);vNormal=normalize(uRotation*(aNormal/uScale));gl_Position=uProjection*vec4(p+vec3(0.0,0.0,-uDistance),1.0);}'));gl.attachShader(program,shader(gl.FRAGMENT_SHADER,'precision mediump float;varying vec3 vNormal;uniform vec3 uColor;void main(){float light=0.35+0.65*abs(dot(normalize(vNormal),normalize(vec3(0.4,0.7,1.0))));gl_FragColor=vec4(uColor*light,1.0);}'));gl.linkProgram(program);if(!gl.getProgramParameter(program,gl.LINK_STATUS))throw Error('三维渲染程序不可用');gl.useProgram(program);gl.enable(gl.DEPTH_TEST);gl.disable(gl.CULL_FACE);
const loc={};['uRotation','uProjection','uScale','uPosition','uSceneScale','uDistance','uColor'].forEach(k=>loc[k]=gl.getUniformLocation(program,k));const pos=gl.getAttribLocation(program,'aPosition'),normal=gl.getAttribLocation(program,'aNormal');
function buffer(accessorIndex,target){const a=gltf.accessors[accessorIndex],v=gltf.bufferViews[a.bufferView],offset=binOffset+(v.byteOffset||0)+(a.byteOffset||0),width=a.type==='VEC3'?3:1;const array=a.componentType===5126?new Float32Array(raw.buffer,offset,a.count*width):new Uint16Array(raw.buffer,offset,a.count);const b=gl.createBuffer();gl.bindBuffer(target,b);gl.bufferData(target,array,gl.STATIC_DRAW);return {buffer:b,count:a.count}}
const meshes=nodes.map(n=>{const p=gltf.meshes[n.mesh].primitives[0],color=gltf.materials[p.material].pbrMetallicRoughness.baseColorFactor;return {node:n,positions:buffer(p.attributes.POSITION,gl.ARRAY_BUFFER),normals:buffer(p.attributes.NORMAL,gl.ARRAY_BUFFER),indices:buffer(p.indices,gl.ELEMENT_ARRAY_BUFFER),color}});
const extent=Math.max(1,...nodes.flatMap(n=>(n.translation||[0,0,0]).map((v,i)=>Math.abs(v)+(n.scale?.[i]||1)/2))),select=$('#scene-part');nodes.forEach((n,i)=>{let option=el('option',n.name||n.extras?.id||String(i));option.value=i;select.append(option)});
let ax=.35,ay=.5,zoom=1,explode=0,selected=-1,drag=null,moved=false,projected=[];
function selectedPart(index){selected=index;select.value=String(index);const node=nodes[index];$('#scene-selection').textContent=node?node.name+'\n'+(node.extras?.description||'')+'\n'+(node.extras?.dimensions_known?'尺寸信息由当前版本提供，仍需核实。':'参数化示意：位置与尺寸不是已测量物理值。')+'\n证据：'+JSON.stringify(node.extras?.evidence_refs||[]):'选择部件查看说明与证据编号';draw()}
function draw(){const dpr=Math.min(2,devicePixelRatio||1),w=Math.max(400,Math.round(canvas.clientWidth*dpr)),h=Math.round(380*dpr);canvas.width=w;canvas.height=h;overlay.width=w;overlay.height=h;gl.viewport(0,0,w,h);gl.clearColor(.063,.122,.208,1);gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);labels.clearRect(0,0,w,h);let cx=Math.cos(ax),sx=Math.sin(ax),cy=Math.cos(ay),sy=Math.sin(ay),rotation=new Float32Array([cy,sx*sy,-cx*sy,0,cx,sx,sy,-sx*cy,cx*cy]),f=1/Math.tan(Math.PI/8),near=.1,far=100,projection=new Float32Array([f/(w/h),0,0,0,0,f,0,0,0,0,(far+near)/(near-far),-1,0,0,2*far*near/(near-far),0]),scale=1.6/(extent*(1+explode*.4)),distance=6/zoom;gl.uniformMatrix3fv(loc.uRotation,false,rotation);gl.uniformMatrix4fv(loc.uProjection,false,projection);gl.uniform1f(loc.uSceneScale,scale);gl.uniform1f(loc.uDistance,distance);projected=[];
meshes.forEach((m,i)=>{let base=m.node.translation||[0,0,0],len=Math.hypot(...base),direction=len?base.map(v=>v/len):[Math.cos(i*2.4),Math.sin(i*2.4),Math.sin(i*1.3)],p=base.map((v,k)=>v+direction[k]*extent*.55*explode),size=m.node.scale||[1,1,1];gl.uniform3fv(loc.uPosition,p);gl.uniform3fv(loc.uScale,size);gl.uniform3fv(loc.uColor,m.color.slice(0,3).map(v=>Math.min(1,v+(selected===i?.23:0))));gl.bindBuffer(gl.ARRAY_BUFFER,m.positions.buffer);gl.enableVertexAttribArray(pos);gl.vertexAttribPointer(pos,3,gl.FLOAT,false,0,0);gl.bindBuffer(gl.ARRAY_BUFFER,m.normals.buffer);gl.enableVertexAttribArray(normal);gl.vertexAttribPointer(normal,3,gl.FLOAT,false,0,0);gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER,m.indices.buffer);gl.drawElements(gl.TRIANGLES,m.indices.count,gl.UNSIGNED_SHORT,0);let x=(p[0]*cy+p[2]*sy)*scale,z=(-p[0]*sy+p[2]*cy)*scale,y=p[1]*scale,ry=y*cx-z*sx,rz=y*sx+z*cx;if(distance-rz>.1)projected.push({i,id:m.node.extras?.id,x:w/2+x*f/(distance-rz)*h/2,y:h/2-ry*f/(distance-rz)*h/2,z:rz,radius:Math.max(16*dpr,Math.max(...size)*scale*f/(distance-rz)*h/3)})});
const points=new Map(projected.map(p=>[p.id,p]));labels.strokeStyle='#b4d4fa77';labels.setLineDash([4*dpr,5*dpr]);(gltf.extras?.relations||[]).forEach(edge=>{const a=points.get(edge.source),b=points.get(edge.target);if(a&&b){labels.beginPath();labels.moveTo(a.x,a.y);labels.lineTo(b.x,b.y);labels.stroke()}});labels.setLineDash([]);if($('#show-labels').checked){labels.font=12*dpr+'px system-ui';projected.forEach(p=>{const text=nodes[p.i].name||p.id||'',width=labels.measureText(text).width;labels.fillStyle='#071321aa';labels.fillRect(p.x+8*dpr,p.y-18*dpr,width+10*dpr,20*dpr);labels.fillStyle=p.i===selected?'#fff2b0':'#e6f3ff';labels.fillText(text,p.x+13*dpr,p.y-4*dpr)})}}
canvas.addEventListener('pointerdown',ev=>{drag={x:ev.clientX,y:ev.clientY};moved=false;canvas.setPointerCapture(ev.pointerId)});canvas.addEventListener('pointermove',ev=>{if(!drag)return;const dx=ev.clientX-drag.x,dy=ev.clientY-drag.y;if(Math.abs(dx)+Math.abs(dy)>2)moved=true;ay+=dx/150;ax+=dy/150;drag={x:ev.clientX,y:ev.clientY};draw()});canvas.addEventListener('pointerup',ev=>{if(!moved){const rect=canvas.getBoundingClientRect(),x=(ev.clientX-rect.left)*canvas.width/rect.width,y=(ev.clientY-rect.top)*canvas.height/rect.height;const candidates=projected.filter(p=>Math.hypot(p.x-x,p.y-y)<p.radius).sort((a,b)=>b.z-a.z);if(candidates.length)selectedPart(candidates[0].i)}drag=null});canvas.addEventListener('wheel',ev=>{ev.preventDefault();zoom=Math.min(2.5,Math.max(.4,zoom*Math.exp(-ev.deltaY/500)));$('#scene-zoom').value=zoom;draw()},{passive:false});select.onchange=()=>selectedPart(Number(select.value));$('#scene-zoom').oninput=ev=>{zoom=Number(ev.target.value);draw()};$('#scene-explode').oninput=ev=>{explode=Number(ev.target.value);draw()};$('#show-labels').onchange=draw;$('#reset-scene').onclick=()=>{ax=.35;ay=.5;zoom=1;explode=0;$('#scene-zoom').value=1;$('#scene-explode').value=0;selectedPart(-1)};window.addEventListener('resize',draw);draw();$('#scene-status').textContent=nodes.length+' 个真实几何网格部件 · 参数化示意；拖动旋转，滚轮缩放，点击或下拉选择部件。';
}catch(error){$('#scene-status').textContent=error.message}})();
"""


def render_html(report: dict, evidence: dict | None = None, reviews: list[dict] | None = None) -> str:
    """No external assets, fetch, CDN, or scripts copied from generated content."""
    evidence = evidence or {}
    manifest = report.get("workflow", {}).get("manifest", {}) if report.get("workflow") else {}
    glb = scene_glb((report.get("workflow") or {}).get("scene"))
    data = json_bytes({"report": report, "evidence": evidence, "manifest": manifest, "reviews": reviews or [],
                      "scene_glb": base64.b64encode(glb).decode() if glb else None,
                      "body_json": json_bytes(report_body(report)).decode(), "evidence_json": json_bytes(evidence).decode()}).decode().replace("<", "\\u003c").replace("&", "\\u0026")
    title = html.escape(report.get("title", "JustREAD 报告"))
    version = int(report.get("version") or 1)
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="referrer" content="no-referrer"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; connect-src 'none'; object-src 'none'; base-uri 'none'"><title>{title}</title><style>{STYLE}</style></head><body>
<header><span class="tag">JustREAD · 固定版本 {version}</span><h1>{title}</h1><p class="muted">离线阅读 · 交互图表 · 版本证据 · 自动检查与人工审查分开记录</p></header>
<main><aside><label for="search">搜索报告正文</label><input id="search" placeholder="关键词"><p id="search-status" class="stat">显示全部章节</p><nav id="navigation"></nav><div class="toolbar"><button id="download-report">报告 JSON</button><button id="download-evidence">证据 JSON</button></div></aside><article>
<div id="report-content"></div><section><h2>交互表达</h2><h3>流程</h3><div id="flow"></div><h3>关系</h3><div id="relations"></div><pre id="visual-detail">选择图中节点查看内容。</pre><div id="charts"></div><h3>三维关系场景</h3><p id="scene-status" class="muted">三维用于观察关系，不代替证据或数值解释。</p><div class="scene-wrap"><canvas id="scene3d" class="scene" aria-label="可旋转三维部件模型"></canvas><canvas id="scene-labels" class="scene-labels"></canvas></div><div class="toolbar scene-tools"><button id="reset-scene">重置视角</button><label>缩放 <input id="scene-zoom" type="range" min="0.4" max="2.5" step="0.05" value="1"></label><label>爆炸 <input id="scene-explode" type="range" min="0" max="2" step="0.05" value="0"></label><label><input id="show-labels" type="checkbox" checked>标签</label><select id="scene-part" aria-label="选择三维部件"><option value="-1">选择部件</option></select></div><pre id="scene-selection">选择部件查看说明与证据编号</pre></section>
<section><h2>检查与交付</h2><p id="review"></p><p id="warnings" class="warning"></p><h3>导出时的版本审查记录</h3><pre id="review-history"></pre><button id="verify">验证正文与证据</button><p id="integrity" role="status">版本内哈希可验证；完整 ZIP 文件清单由 verify.py 校验。</p><details><summary>版本 manifest</summary><pre id="manifest"></pre></details><details><summary>本版本证据记录</summary><pre id="evidence"></pre></details></section>
</article></main><script id="justread-data" type="application/json">{data}</script><script>{READER_SCRIPT.replace('__SCENE_RENDERER__', SCENE_RENDERER)}</script></body></html>'''


VERIFY_SCRIPT = '''"""Run with Python 3 inside the extracted offline report directory."""
import hashlib, json
from pathlib import Path
root = Path(__file__).resolve().parent
manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
errors = []
for name, expected in manifest["files"].items():
    path = (root / name).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        errors.append(name + ": missing or invalid path")
        continue
    raw = path.read_bytes()
    if len(raw) != expected["size_bytes"] or hashlib.sha256(raw).hexdigest() != expected["sha256"]:
        errors.append(name + ": hash mismatch")
if errors:
    raise SystemExit("\\n".join(errors))
print("All " + str(len(manifest["files"])) + " files match the fixed-version manifest.")
'''


def package_report(report: dict, evidence: dict | None = None, reviews: list[dict] | None = None) -> tuple[bytes, dict]:
    """Build and verify all file hashes before returning an immutable ZIP."""
    evidence = evidence or {}
    reviews = reviews or []
    files = {"index.html": render_html(report, evidence, reviews).encode("utf-8"),
             "report.json": json_bytes(report), "evidence.json": json_bytes(evidence),
             "reviews.json": json_bytes(reviews),
             "verify.py": VERIFY_SCRIPT.encode("utf-8"),
             "README.txt": "解压后直接打开 index.html，无需网络。运行 python3 verify.py 验证全部文件。manifest.json 不包含自身哈希；版本正文哈希排除 bookmarks 和 workflow.manifest。reviews.json 冻结导出时的该版本审查记录，后续审查需要重新导出。scene.glb 如存在，是无外链的参数化示意模型，不代表已测量物理尺寸。自动一致性检查不等于人工事实审查。\n".encode("utf-8")}
    scene = (report.get("workflow") or {}).get("scene")
    glb = scene_glb(scene)
    if glb:
        files["scene.glb"] = glb
        files["scene.json"] = json_bytes(scene)
    manifest = {"schema": "justread.offline-manifest.v1", "report_id": report["id"], "version": int(report.get("version") or 1),
                "entry": "index.html", "hash_algorithm": "sha256", "review_snapshot_hash": digest(reviews),
                "export_id": digest({"report": digest(report), "reviews": digest(reviews)}), "files": {
                    name: {"sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw)} for name, raw in files.items()}}
    files["manifest.json"] = json_bytes(manifest)
    output = io.BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for name, raw in files.items():
            archive.writestr(name, raw)
    blob = output.getvalue()
    with ZipFile(io.BytesIO(blob)) as archive:
        assert archive.testzip() is None
        for name, metadata in manifest["files"].items():
            raw = archive.read(name)
            if hashlib.sha256(raw).hexdigest() != metadata["sha256"]:
                raise ValueError("Offline package failed its own integrity check")
        if b'id="justread-data"' not in archive.read("index.html"):
            raise ValueError("Offline package is missing the embedded report")
    return blob, manifest
