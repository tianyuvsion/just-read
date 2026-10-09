import base64
import hashlib
from io import BytesIO
from zipfile import ZipFile

import pytest
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

from just_read.sources import decode_upload, parse_upload
from just_read.storage import StoreError


def text_pdf():
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'),
        NameObject('/BaseFont'): NameObject('/Helvetica')})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
    stream = DecodedStreamObject()
    stream.set_data(b'BT /F1 14 Tf 40 720 Td (Simulated total is 50.) Tj ET')
    page[NameObject('/Contents')] = writer._add_object(stream)
    out = BytesIO(); writer.write(out)
    return out.getvalue()


def test_utf8_original_hash_chunk_offsets_and_overlap():
    text = '# 模拟材料\n\n' + '模拟段落' * 600 + '\n\n尾段数据为 50。'
    raw = text.encode()
    result = parse_upload('../研究材料.md', raw)
    assert result['name'] == '研究材料.md'
    assert result['hash'] == hashlib.sha256(raw).hexdigest()
    assert result['chunks'][-1]['paragraph'] == 3
    for chunk in result['chunks']:
        assert text[chunk['start']:chunk['end']] == chunk['text']
    second, third = result['chunks'][1:3]
    assert second['text'][-150:] == third['text'][:150]
    assert decode_upload(base64.b64encode(raw).decode()) == raw


def test_docx_paragraphs_and_tables_without_invented_page_numbers():
    buffer = BytesIO()
    with ZipFile(buffer, 'w') as doc:
        doc.writestr('word/document.xml', '''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>模拟第一段</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>表格模拟 20</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>''')
    result = parse_upload('模拟.docx', buffer.getvalue())
    assert [c['text'] for c in result['chunks']] == ['模拟第一段', '表格模拟 20']
    assert [c['paragraph'] for c in result['chunks']] == [1, 2]
    assert all(c['page'] is None for c in result['chunks'])


def test_pdf_real_text_page_locator_does_not_call_ocr(monkeypatch):
    monkeypatch.setattr('just_read.sources._ocr_pdf', lambda *_: pytest.fail('text PDF must not OCR'))
    result = parse_upload('模拟.pdf', text_pdf())
    assert result['chunks'][0]['page'] == 1
    assert 'total is 50' in result['chunks'][0]['text']
    assert result['chunks'][0]['method'] == 'pdf_text'


def test_scanned_pdf_ocr_locator_and_explicit_warning(monkeypatch):
    writer = PdfWriter(); writer.add_blank_page(612, 792)
    raw = BytesIO(); writer.write(raw)
    def ocr(original, numbers):
        assert original == raw.getvalue() and numbers == [1]
        return [{'page': 1, 'text': '模拟 OCR 数字 42', 'confidence': .7}], 'simulated_ocr'
    monkeypatch.setattr('just_read.sources._ocr_pdf', ocr)
    result = parse_upload('扫描.pdf', raw.getvalue())
    assert result['extraction'] == 'simulated_ocr'
    assert result['chunks'][0]['page'] == 1 and result['chunks'][0]['method'] == 'ocr'
    assert any('复核' in warning for warning in result['warnings'])


@pytest.mark.parametrize('name,raw', [('bad.pdf', b'invalid'), ('bad.docx', b'invalid'), ('bad.json', b'{bad'), ('bad.exe', b'abc'), ('bad.txt', b'\xff')])
def test_invalid_material_returns_bounded_error(name, raw):
    with pytest.raises(StoreError) as failure:
        parse_upload(name, raw)
    assert failure.value.status == 422


@pytest.mark.parametrize('value', ['', '@@@@'])
def test_invalid_base64(value):
    with pytest.raises(StoreError) as failure:
        decode_upload(value)
    assert failure.value.status == 422
