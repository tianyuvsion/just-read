"""Original uploads, page/paragraph text extraction and local OCR.

Extraction does not establish truth. The original bytes and their hash remain
available so that each cited location can be checked against the same revision.
"""
from __future__ import annotations

import base64
import binascii
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
import xml.etree.ElementTree as ET
import zipfile

from pypdf import PdfReader

from .storage import StoreError

MAX_UPLOAD = 20 * 1024 * 1024
MAX_TEXT = 1_000_000
MIME = {"pdf": "application/pdf", "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "md": "text/markdown", "markdown": "text/markdown", "txt": "text/plain", "csv": "text/csv", "json": "application/json"}


def decode_upload(encoded: str) -> bytes:
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise StoreError(422, "INVALID_FILE", "文件编码无效，请重新选择文件。") from exc
    if not raw:
        raise StoreError(422, "EMPTY_FILE", "文件为空。")
    if len(raw) > MAX_UPLOAD:
        raise StoreError(413, "FILE_TOO_LARGE", "研究材料最大 20 MB。")
    return raw


def _ocr_pdf(raw: bytes, page_numbers: list[int]) -> tuple[list[dict], str]:
    if not page_numbers:
        return [], "not_needed"
    with tempfile.TemporaryDirectory(prefix="justread-ocr-") as directory:
        source = Path(directory) / "source.pdf"
        source.write_bytes(raw)
        try:
            if sys.platform == "darwin" and shutil.which("swift"):
                # The module cache is private to the task, not an assumed user Xcode setup.
                cache = Path(tempfile.gettempdir()) / "justread-swift-module-cache"
                cache.mkdir(exist_ok=True)
                env = {**os.environ, "CLANG_MODULE_CACHE_PATH": str(cache), "SWIFT_MODULECACHE_PATH": str(cache)}
                output = subprocess.run(["swift", str(Path(__file__).with_name("ocr.swift")), str(source), json.dumps(page_numbers)],
                                        capture_output=True, timeout=180, env=env, check=True)
                return json.loads(output.stdout), "apple_vision"
            if shutil.which("pdftoppm") and shutil.which("tesseract"):
                result = []
                for number in page_numbers:
                    stem = Path(directory) / f"page-{number}"
                    subprocess.run(["pdftoppm", "-f", str(number), "-l", str(number), "-r", "180", "-png", "-singlefile", str(source), str(stem)],
                                   capture_output=True, timeout=45, check=True)
                    text = subprocess.run(["tesseract", str(stem) + ".png", "stdout", "-l", "chi_sim+eng"],
                                          capture_output=True, timeout=60, check=True).stdout.decode("utf-8", "replace")
                    result.append({"page": number, "text": text, "confidence": None})
                return result, "tesseract"
        except (OSError, subprocess.SubprocessError, ValueError):
            return [], "failed"
    return [], "unavailable"


def parse_upload(name: str, raw: bytes) -> dict:
    name = Path(name.replace("\\", "/")).name.strip()[:300]
    extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if extension not in MIME:
        raise StoreError(422, "UNSUPPORTED_FILE", "支持 PDF、DOCX、Markdown、TXT、CSV 或 JSON 材料。")
    pages, warnings = [], []
    extraction = "text"
    try:
        if extension == "pdf":
            reader = PdfReader(BytesIO(raw), strict=False)
            if reader.is_encrypted and not reader.decrypt(""):
                raise StoreError(422, "ENCRYPTED_FILE", "PDF 已加密，请先提供可读取的副本。")
            if len(reader.pages) > 300:
                raise StoreError(413, "TOO_MANY_PAGES", "PDF 超过 300 页，请拆分后添加。")
            for index, page in enumerate(reader.pages, 1):
                pages.append({"page": index, "paragraph": None, "text": (page.extract_text() or "").strip(), "method": "pdf_text"})
            scanned = [p["page"] for p in pages if not p["text"].strip()]
            if scanned:
                ocr, extraction = _ocr_pdf(raw, scanned)
                recovered = {p["page"]: p for p in ocr}
                for page in pages:
                    if page["page"] in recovered:
                        page.update(text=recovered[page["page"]]["text"].strip(), method="ocr", ocr_confidence=recovered[page["page"]].get("confidence"))
                warnings.append("部分 PDF 页面使用 OCR 提取，数字、符号与引用需对照原页复核。")
                if any(not p["text"] for p in pages):
                    warnings.append("部分页面仍未识别到文字，原始文件已保留，可补充文字材料。")
        elif extension == "docx":
            with zipfile.ZipFile(BytesIO(raw)) as archive:
                if sum(item.file_size for item in archive.infolist()) > 80 * 1024 * 1024:
                    raise StoreError(413, "EXPANDED_FILE_TOO_LARGE", "文档解压后超过处理范围。")
                content = archive.read("word/document.xml")
                tree = ET.fromstring(content)
                ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
                for index, paragraph in enumerate(tree.findall(".//w:p", ns), 1):
                    text = "".join(node.text or "" for node in paragraph.findall(".//w:t", ns)).strip()
                    if text:
                        pages.append({"page": None, "paragraph": index, "text": text, "method": "docx_xml"})
            warnings.append("DOCX 按段落定位；页码由 Word 排版决定，不自动推测页码。")
        else:
            text = raw.decode("utf-8-sig")
            pages = [{"page": None, "paragraph": None, "text": text, "method": "utf8"}]
            if extension == "json":
                json.loads(text)  # The original JSON can contain model/dataset contracts.
    except StoreError:
        raise
    except Exception as exc:
        raise StoreError(422, "INVALID_FILE", "文件无法解析，请检查格式、编码或文件是否损坏。") from exc
    if sum(len(page["text"]) for page in pages) > MAX_TEXT:
        raise StoreError(413, "TEXT_TOO_LARGE", "材料正文超过 100 万字符，请拆分后添加。")
    if not any(page["text"].strip() for page in pages):
        warnings.append("没有可用正文；材料原件已保存，此来源不能提供文字证据。")
    source_id = str(uuid.uuid4())
    chunks = []
    paragraph_number = 0
    for page in pages:
        text = page["text"].replace("\r\n", "\n").replace("\x00", "")
        page["text"] = text
        offset = 0
        for part in re.split(r"\n\s*\n", text):
            stripped = part.strip()
            if not stripped:
                continue
            start = text.find(stripped, offset)
            offset = start + len(stripped)
            paragraph_number += 1
            paragraph = page.get("paragraph") or paragraph_number
            for begin in range(0, len(stripped), 1350):
                chunk = stripped[begin:begin + 1500]
                chunks.append({"id": f"{source_id}-c{len(chunks) + 1}", "text": chunk,
                    "page": page["page"], "paragraph": paragraph, "start": start + begin,
                    "end": start + begin + len(chunk), "method": page["method"]})
    return {"id": source_id, "name": name, "title": name, "media_type": MIME[extension],
        "kind": "upload", "hash": hashlib.sha256(raw).hexdigest(), "revision": 1,
        "createdAt": datetime.now(timezone.utc).isoformat(), "size_bytes": len(raw),
        "pages": pages, "chunks": chunks, "warnings": warnings, "extraction": extraction,
        "text_characters": sum(len(page["text"]) for page in pages)}
