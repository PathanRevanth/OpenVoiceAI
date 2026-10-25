"""Attachment text extraction for chat uploads.

Models differ in which binary media types they accept. Text-like files and
Office Open XML documents are therefore flattened to plain text before the run,
so a DOCX, XLSX, PPTX, CSV, or source file works with every chat model. Images,
PDFs, audio, and video still travel as native binary content.

Only the Python standard library is used; no document parser dependency is
needed in the container.
"""

from __future__ import annotations

import io
import re
import zipfile
from html import escape
from xml.etree import ElementTree as ET

# Keep a single attachment from eating the model's whole context window.
MAX_EXTRACTED_CHARS = 120_000

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"

# Legacy binary Office formats cannot be parsed with the standard library.
# Recover their printable text runs so the model still sees the document.
_LEGACY_OFFICE = {
    "application/msword",
    "application/vnd.ms-excel",
    "application/vnd.ms-powerpoint",
}

_TEXTUAL_TYPES = {
    "application/json",
    "application/xml",
    "application/javascript",
    "application/x-javascript",
    "application/typescript",
    "application/x-yaml",
    "application/yaml",
    "application/sql",
    "application/graphql",
}

# Extracted text is wrapped in this tag so the model can tell attachments from
# the user's own words. Names are escaped to keep the wrapper well-formed.
_TEXT_WRAPPER = '<attached-file name="{name}" type="{media_type}">\n{body}\n</attached-file>'


def is_textual(media_type: str) -> bool:
    media = (media_type or "").lower()
    return media.startswith("text/") or media in _TEXTUAL_TYPES


def extract_attachment_text(name: str, media_type: str, data: bytes) -> str | None:
    """Return plain text for text/Office files, or None for native media."""
    media = (media_type or "").lower()
    if is_textual(media):
        return _decode_text(data)
    if media == DOCX:
        return _docx_text(data)
    if media == XLSX:
        return _xlsx_text(data)
    if media == PPTX:
        return _pptx_text(data)
    if media in _LEGACY_OFFICE:
        return _legacy_office_text(data)
    return None


def wrap_attachment_text(name: str, media_type: str, text: str) -> str:
    return _TEXT_WRAPPER.format(
        name=escape(name or "attachment", quote=True),
        media_type=escape(media_type or "application/octet-stream", quote=True),
        body=text,
    )


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8", "utf-16", "latin-1"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:  # pragma: no cover - latin-1 always succeeds
        text = data.decode("utf-8", errors="replace")
    text = text.replace("\x00", "")
    return _cap(text)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _xml_text(xml: bytes) -> str:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return ""
    paragraphs: list[str] = []
    for node in root.iter():
        if _local(node.tag) != "p":
            continue
        parts = [child.text or "" for child in node.iter() if _local(child.tag) == "t"]
        text = "".join(parts).strip()
        if text:
            paragraphs.append(text)
    return "\n".join(paragraphs)


def _docx_text(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            xml = archive.read("word/document.xml")
    except (KeyError, zipfile.BadZipFile, OSError):
        return ""
    return _cap(_xml_text(xml))


def _column_index(cell_ref: str) -> int:
    letters = re.match(r"[A-Za-z]+", cell_ref or "")
    if not letters:
        return 0
    value = 0
    for char in letters.group(0).upper():
        value = value * 26 + (ord(char) - 64)
    return max(0, value - 1)


def _xlsx_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    except (KeyError, ET.ParseError):
        return []
    out: list[str] = []
    for item in root.iter():
        if _local(item.tag) != "si":
            continue
        out.append("".join(node.text or "" for node in item.iter() if _local(node.tag) == "t"))
    return out


def _xlsx_text(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            shared = _xlsx_shared_strings(archive)
            sheets = sorted(
                (name for name in archive.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", name)),
                key=lambda name: int(re.search(r"(\d+)", name.rsplit("/", 1)[-1]).group(1)),
            )
            blocks: list[str] = []
            for number, sheet in enumerate(sheets, start=1):
                root = ET.fromstring(archive.read(sheet))
                rows: list[str] = []
                for row in root.iter():
                    if _local(row.tag) != "row":
                        continue
                    cells: dict[int, str] = {}
                    for cell in row.iter():
                        if _local(cell.tag) != "c":
                            continue
                        index = _column_index(cell.get("r", ""))
                        kind = cell.get("t", "")
                        value = ""
                        if kind == "inlineStr":
                            value = "".join(
                                node.text or "" for node in cell.iter() if _local(node.tag) == "t"
                            )
                        else:
                            raw = next(
                                (node.text or "" for node in cell.iter() if _local(node.tag) == "v"),
                                "",
                            )
                            if kind == "s" and raw.isdigit():
                                pos = int(raw)
                                value = shared[pos] if 0 <= pos < len(shared) else ""
                            else:
                                value = raw
                        cells[index] = value.replace("\n", " ")
                    if cells:
                        width = max(cells) + 1
                        rows.append("\t".join(cells.get(i, "") for i in range(width)).rstrip())
                if rows:
                    blocks.append(f"--- Sheet {number} ---\n" + "\n".join(rows))
            return _cap("\n\n".join(blocks))
    except (ET.ParseError, zipfile.BadZipFile, OSError):
        return ""


def _pptx_text(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            slides = sorted(
                (name for name in archive.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", name)),
                key=lambda name: int(re.search(r"(\d+)", name.rsplit("/", 1)[-1]).group(1)),
            )
            blocks: list[str] = []
            for number, slide in enumerate(slides, start=1):
                text = _xml_text(archive.read(slide))
                if text:
                    blocks.append(f"--- Slide {number} ---\n{text}")
            return _cap("\n\n".join(blocks))
    except (ET.ParseError, zipfile.BadZipFile, OSError):
        return ""


def _legacy_office_text(data: bytes) -> str:
    """Best-effort text recovery for .doc/.xls/.ppt binary containers.

    These formats store text as printable runs inside an OLE compound file.
    Scanning for runs in both single-byte and UTF-16LE form recovers the body
    without pulling in a heavyweight Office parser.
    """
    best = ""
    for encoding in ("utf-16-le", "cp1252"):
        decoded = data.decode(encoding, errors="ignore")
        runs = re.findall(r"[\x20-\x7e\t\r\n]{4,}", decoded)
        joined = "\n".join(run.strip() for run in runs if run.strip())
        if len(joined) > len(best):
            best = joined
    return _cap(best)


def _cap(text: str) -> str:
    text = text.strip()
    if len(text) <= MAX_EXTRACTED_CHARS:
        return text
    return text[:MAX_EXTRACTED_CHARS] + "\n\n[attachment truncated to fit the context window]"
