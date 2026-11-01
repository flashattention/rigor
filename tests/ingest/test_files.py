import io
import zipfile

import pytest

from rigor.ingest import files
from rigor.ingest.files import FileFormat, IngestError, extract_text


def make_docx(paragraphs: list[str], padding: int = 0) -> bytes:
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("word/document.xml", xml)
        if padding:
            zf.writestr("word/media/pad.bin", b"\0" * padding)
    return buf.getvalue()


def make_pdf(text: str | None) -> bytes:
    """최소 PDF. text=None이면 텍스트가 없는(스캔본 같은) 페이지."""
    content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode() if text else b""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % i + obj + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1))
    for off in offsets:
        out.write(b"%010d 00000 n \n" % off)
    out.write(
        b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    )
    return out.getvalue()


def test_plain_text_utf8_and_cp949():
    assert extract_text("이력서 본문".encode()).text == "이력서 본문"
    assert extract_text("이력서 본문".encode("cp949")).text == "이력서 본문"


def test_docx_paragraphs_are_extracted():
    result = extract_text(make_docx(["멀티 에이전트 프로젝트", "LangGraph 사용"]))
    assert result.format is FileFormat.DOCX
    assert result.text == "멀티 에이전트 프로젝트\nLangGraph 사용"


def test_text_pdf_is_extracted():
    result = extract_text(make_pdf("Built a multi agent research assistant with LangGraph"))
    assert result.format is FileFormat.PDF_TEXT
    assert "LangGraph" in result.text and not result.needs_ocr


def test_pdf_without_text_is_routed_to_ocr():
    result = extract_text(make_pdf(None))
    assert result.format is FileFormat.PDF_SCAN and result.needs_ocr


def test_image_is_routed_to_ocr():
    assert extract_text(b"\x89PNG\r\n\x1a\n" + b"x" * 100).needs_ocr


@pytest.mark.parametrize(
    "data",
    [b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"x" * 100, None],
    ids=["hwp", "hwpx"],
)
def test_hwp_formats_are_rejected_with_guidance(data):
    if data is None:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("Contents/section0.xml", "<x/>")
        data = buf.getvalue()
    with pytest.raises(IngestError, match="PDF로 저장"):
        extract_text(data)


def test_size_and_unzip_limits(monkeypatch):
    with pytest.raises(IngestError, match="너무 큽니다"):
        extract_text(b"a" * (files.MAX_BYTES + 1))
    monkeypatch.setattr(files, "MAX_UNZIPPED_BYTES", 1000)
    with pytest.raises(IngestError, match="압축 해제"):
        extract_text(make_docx(["x"], padding=5000))


def test_binary_and_empty_rejected():
    with pytest.raises(IngestError):
        extract_text(b"\x00\x01\x02 binary")
    with pytest.raises(IngestError):
        extract_text(b"   ")
