"""Attachment extraction and upload-schema tests."""

from __future__ import annotations

import base64
import io
import zipfile

import pytest
from pydantic import ValidationError

from voicefirst.api.attachments import extract_attachment_text, wrap_attachment_text
from voicefirst.api.schemas import ChatAttachment

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


def _archive(parts: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, text in parts.items():
            archive.writestr(name, text)
    return buffer.getvalue()


def test_docx_is_flattened_to_text() -> None:
    data = _archive(
        {
            "word/document.xml": (
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                "<w:body><w:p><w:r><w:t>Hello </w:t></w:r><w:r><w:t>world</w:t></w:r></w:p>"
                "<w:p><w:r><w:t>Second line</w:t></w:r></w:p></w:body></w:document>"
            )
        }
    )
    assert extract_attachment_text("notes.docx", DOCX, data) == "Hello world\nSecond line"


def test_xlsx_is_flattened_to_tsv() -> None:
    data = _archive(
        {
            "xl/sharedStrings.xml": (
                '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                "<si><t>Name</t></si><si><t>Alice</t></si></sst>"
            ),
            "xl/worksheets/sheet1.xml": (
                '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                '<sheetData><row r="1"><c r="A1" t="s"><v>0</v></c></row>'
                '<row r="2"><c r="A2" t="s"><v>1</v></c><c r="B2"><v>42</v></c></row>'
                "</sheetData></worksheet>"
            ),
        }
    )
    text = extract_attachment_text("sheet.xlsx", XLSX, data)
    assert text is not None
    assert "--- Sheet 1 ---" in text
    assert "Name" in text
    assert "Alice\t42" in text


def test_pptx_is_flattened_to_text() -> None:
    data = _archive(
        {
            "ppt/slides/slide1.xml": (
                '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
                'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
                "<p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>Title slide</a:t></a:r></a:p>"
                "</p:txBody></p:sp></p:spTree></p:cSld></p:sld>"
            )
        }
    )
    assert extract_attachment_text("deck.pptx", PPTX, data) == "--- Slide 1 ---\nTitle slide"


def test_source_file_is_decoded() -> None:
    assert extract_attachment_text("app.py", "text/x-python", b"print('hi')") == "print('hi')"


def test_legacy_office_text_is_recovered() -> None:
    data = "Legacy memo\nSecond line".encode("utf-16-le")
    text = extract_attachment_text("old.doc", "application/msword", data)
    assert text is not None
    assert "Legacy memo" in text
    assert "Second line" in text


def test_native_media_is_left_for_the_model() -> None:
    for media_type in ("image/png", "application/pdf", "audio/mpeg", "video/mp4"):
        assert extract_attachment_text("file", media_type, b"binary") is None


def test_wrapper_escapes_the_file_name() -> None:
    wrapped = wrap_attachment_text('a"b.txt', "text/plain", "body")
    assert 'name="a&quot;b.txt"' in wrapped
    assert "body" in wrapped


def test_schema_accepts_supported_media() -> None:
    payload = base64.b64encode(b"x").decode()
    for media_type in (
        "image/png",
        "application/pdf",
        "audio/mpeg",
        "video/mp4",
        "text/x-python",
        "application/json",
        "application/msword",
        "application/vnd.ms-powerpoint",
        DOCX,
        XLSX,
        PPTX,
    ):
        ChatAttachment(name="file", media_type=media_type, data=payload)


def test_schema_rejects_unknown_media() -> None:
    with pytest.raises(ValidationError):
        ChatAttachment(
            name="file",
            media_type="application/zip",
            data=base64.b64encode(b"x").decode(),
        )


def test_preview_must_be_an_image_data_url() -> None:
    payload = base64.b64encode(b"x").decode()
    with pytest.raises(ValidationError):
        ChatAttachment(
            name="file",
            media_type="image/png",
            data=payload,
            preview="https://example.com/a.png",
        )
    ChatAttachment(
        name="file",
        media_type="image/png",
        data=payload,
        preview="data:image/jpeg;base64,AAAA",
    )
