"""업로드 파일 → 텍스트 (ADR-0011). 업로드 파일은 신뢰할 수 없는 입력이다.

- 형식은 확장자가 아니라 파일 시그니처(매직 바이트)로 판정한다.
- 크기·페이지·압축 해제 크기에 상한을 둔다 (압축 폭탄 방지).
- 스캔 PDF(텍스트가 거의 없음)와 이미지는 OCR 경로로 보낸다 (M1b).
- HWP/HWPX는 M1b에서 지원한다. 지금은 명확한 안내와 함께 거절한다.
"""

from __future__ import annotations

import io
import unicodedata
import zipfile
from dataclasses import dataclass, field
from enum import StrEnum

from defusedxml import ElementTree

MAX_BYTES = 10 * 1024 * 1024
MAX_PAGES = 10
MAX_UNZIPPED_BYTES = 50 * 1024 * 1024
MIN_CHARS_PER_PAGE = 30  # 이보다 적으면 스캔 PDF로 본다

_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class FileFormat(StrEnum):
    TEXT = "text"
    PDF_TEXT = "pdf_text"
    PDF_SCAN = "pdf_scan"
    DOCX = "docx"
    HWP = "hwp"
    HWPX = "hwpx"
    IMAGE = "image"


class IngestError(ValueError):
    """사용자에게 그대로 보여 줄 수 있는 거절 사유."""


@dataclass
class ExtractedText:
    format: FileFormat
    text: str
    pages: int = 1
    needs_ocr: bool = False
    warnings: list[str] = field(default_factory=list)


def detect_format(data: bytes) -> FileFormat:
    if data.startswith(b"%PDF-"):
        return FileFormat.PDF_TEXT
    if data.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return FileFormat.HWP  # OLE 복합 문서 (HWP 5.x)
    if data.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff")):
        return FileFormat.IMAGE
    if data.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                names = set(zf.namelist())
        except zipfile.BadZipFile as exc:
            raise IngestError("손상된 압축 파일입니다.") from exc
        if "word/document.xml" in names:
            return FileFormat.DOCX
        if any(n.startswith("Contents/section") for n in names):
            return FileFormat.HWPX
        raise IngestError("지원하지 않는 압축 문서 형식입니다.")
    if b"\x00" in data[:4096]:
        raise IngestError("지원하지 않는 바이너리 파일입니다.")
    return FileFormat.TEXT


def extract_text(data: bytes) -> ExtractedText:
    if len(data) > MAX_BYTES:
        raise IngestError(f"파일이 너무 큽니다 (최대 {MAX_BYTES // (1024 * 1024)}MB).")
    if not data.strip():
        raise IngestError("빈 파일입니다.")
    fmt = detect_format(data)
    if fmt is FileFormat.TEXT:
        return ExtractedText(fmt, _normalize(_decode_text(data)))
    if fmt is FileFormat.PDF_TEXT:
        return _extract_pdf(data)
    if fmt is FileFormat.DOCX:
        return ExtractedText(fmt, _normalize(_extract_docx(data)))
    if fmt in (FileFormat.HWP, FileFormat.HWPX):
        raise IngestError(
            "HWP/HWPX 지원은 준비 중입니다. PDF로 저장해 올리거나 텍스트를 붙여넣어 주세요."
        )
    return ExtractedText(fmt, "", needs_ocr=True, warnings=["이미지 파일은 OCR이 필요합니다."])


def _normalize(text: str) -> str:
    """NFKC: PDF의 합자(ﬂ, ﬁ, ﬃ)를 풀어 URL·기술명이 인식되게 한다."""
    return unicodedata.normalize("NFKC", text)


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp949"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise IngestError("텍스트 인코딩을 읽을 수 없습니다 (UTF-8 또는 CP949만 지원).")


def _extract_pdf(data: bytes) -> ExtractedText:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise IngestError("암호가 걸린 PDF는 읽을 수 없습니다.")
        pages = len(reader.pages)
        if pages > MAX_PAGES:
            raise IngestError(f"페이지가 너무 많습니다 (최대 {MAX_PAGES}쪽).")
        texts = [(page.extract_text() or "") for page in reader.pages]
    except PdfReadError as exc:
        raise IngestError("PDF를 읽을 수 없습니다.") from exc
    text = _normalize("\n".join(texts)).strip()
    if len(text) < MIN_CHARS_PER_PAGE * max(pages, 1):
        return ExtractedText(
            FileFormat.PDF_SCAN,
            text,
            pages,
            needs_ocr=True,
            warnings=["텍스트가 거의 없는 PDF입니다. 스캔 문서로 보고 OCR이 필요합니다."],
        )
    return ExtractedText(FileFormat.PDF_TEXT, text, pages)


def _extract_docx(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        total = sum(i.file_size for i in zf.infolist())
        if total > MAX_UNZIPPED_BYTES:
            raise IngestError("압축 해제 크기가 너무 큽니다.")
        xml = zf.read("word/document.xml")
    root = ElementTree.fromstring(xml)
    paragraphs = []
    for p in root.iter(f"{_W_NS}p"):
        text = "".join(t.text or "" for t in p.iter(f"{_W_NS}t"))
        if text.strip():
            paragraphs.append(text)
    return "\n".join(paragraphs)
