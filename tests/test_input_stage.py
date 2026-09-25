from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from image_downloader.input import (
    InputMetrics,
    SourceType,
    consolidate_urls,
    extract_urls_from_pptx,
    extract_urls_from_text,
)
from image_downloader.input.exceptions import InvalidPowerPointError


def test_extract_text_with_no_urls() -> None:
    result = extract_urls_from_text("texto sem link")
    assert result == []


def test_extract_text_with_multiple_urls_and_duplicates() -> None:
    text = (
        "https://example.com/a\n"
        "texto https://example.com/b (https://example.com/a)\n"
        "https://example.com/a, https://example.com/c."
    )
    result = extract_urls_from_text(text)
    assert len(result) >= 4
    assert any(item.original_url == "https://example.com/a" for item in result)
    assert any(item.original_url == "https://example.com/b" for item in result)
    assert any(item.original_url == "https://example.com/c" for item in result)


def test_extract_text_with_punctuation_and_query() -> None:
    text = "(https://example.com/item?x=1&y=2#frag), https://example.com/other/;"
    result = extract_urls_from_text(text)
    urls = [item.normalized_url for item in result]
    assert "https://example.com/item?x=1&y=2#frag" in urls
    assert "https://example.com/other" in urls


def test_consolidate_preserves_multiple_origins() -> None:
    occurrences = [
        extract_urls_from_text("https://example.com/item/123")[0],
        extract_urls_from_text("https://example.com/item/123")[0],
    ]
    consolidated = consolidate_urls(occurrences)
    assert len(consolidated) == 1
    assert len(consolidated[0].occurrences) == 2
    assert consolidated[0].normalized_url == "https://example.com/item/123"


def test_consolidate_real_pptx_and_pasted_text_sources() -> None:
    path = _write_pptx_with_payload(
        {
            "ppt/slides/slide1.xml": (
                "<p:sld xmlns:p=\"http://schemas.openxmlformats.org/presentationml/2006/main\" "
                "xmlns:a=\"http://schemas.openxmlformats.org/drawingml/2006/main\">"
                "<p:cSld><p:spTree><p:sp><p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r>"
                "<a:t>Shared https://example.com/shared-item</a:t></a:r></a:p></p:txBody></p:sp>"
                "</p:spTree></p:cSld></p:sld>"
            )
        }
    )
    try:
        from_pptx = extract_urls_from_pptx(path)
        from_text = extract_urls_from_text("https://example.com/shared-item")
        merged = consolidate_urls([*from_pptx[0].occurrences, *from_text])
        assert len(merged) == 1
        assert len(merged[0].occurrences) == 2
        origin_types = {occ.source_type for occ in merged[0].occurrences}
        assert {SourceType.slide_text, SourceType.pasted_text}.issubset(origin_types)
    finally:
        path.unlink(missing_ok=True)


def _write_pptx_with_payload(payload: dict[str, str]) -> Path:
    path = Path("tests/.tmp_fixture.pptx")
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types></Types>")
        zf.writestr("_rels/.rels", "<Relationships></Relationships>")
        zf.writestr("docProps/core.xml", "<cp:coreProperties></cp:coreProperties>")
        zf.writestr("ppt/presentation.xml", "<p:presentation></p:presentation>")
        zf.writestr("ppt/_rels/presentation.xml.rels", "<Relationships></Relationships>")
        for name, xml_body in payload.items():
            zf.writestr(name, xml_body)
    return path


def test_extract_urls_from_pptx_slide_text_and_hyperlink() -> None:
    path = _write_pptx_with_payload(
        {
            "ppt/slides/slide1.xml": (
                "<p:sld xmlns:p=\"http://schemas.openxmlformats.org/presentationml/2006/main\" "
                "xmlns:a=\"http://schemas.openxmlformats.org/drawingml/2006/main\">"
                "<p:cSld><p:spTree><p:sp><p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:t>Visit https://example.com/slide</a:t></a:r></a:p></p:txBody></p:sp>"
                "</p:spTree></p:cSld></p:sld>"
            ),
            "ppt/slides/_rels/slide1.xml.rels": (
                "<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">"
                "<Relationship Id='rId1' "
                "Type='http://schemas.openxmlformats.org/officeDocument/2006/"
                "relationships/hyperlink' "
                "Target='https://example.com/hyperlink'/></Relationships>"
            ),
        }
    )
    try:
        result = extract_urls_from_pptx(path)
        urls = [record.normalized_url for record in result]
        assert "https://example.com/slide" in urls
        assert "https://example.com/hyperlink" in urls
    finally:
        path.unlink(missing_ok=True)


def test_extract_urls_from_pptx_comment_and_note() -> None:
    path = _write_pptx_with_payload(
        {
            "ppt/comments/comment1.xml": (
                "<p:cm xmlns:p=\"http://schemas.openxmlformats.org/presentationml/2006/main\" "
                "xmlns:a=\"http://schemas.openxmlformats.org/drawingml/2006/main\">"
                "<p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:t>Comment: "
                "https://example.com/comment</a:t></a:r></a:p></p:txBody></p:cm>"
            ),
            "ppt/notesSlides/notesSlide1.xml": (
                "<p:notes xmlns:p=\"http://schemas.openxmlformats.org/presentationml/2006/main\" "
                "xmlns:a=\"http://schemas.openxmlformats.org/drawingml/2006/main\">"
                "<p:cSld><p:spTree><p:sp><p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r>"
                "<a:t>Note url https://example.com/note</a:t></a:r></a:p></p:txBody></p:sp>"
                "</p:spTree></p:cSld></p:notes>"
            ),
        }
    )
    try:
        result = extract_urls_from_pptx(path)
        urls = [record.normalized_url for record in result]
        assert "https://example.com/comment" in urls
        assert "https://example.com/note" in urls
    finally:
        path.unlink(missing_ok=True)


def test_extract_pptx_same_url_across_two_slide_entries() -> None:
    path = _write_pptx_with_payload(
        {
            "ppt/slides/slide1.xml": (
                "<p:sld xmlns:p=\"http://schemas.openxmlformats.org/presentationml/2006/main\" "
                "xmlns:a=\"http://schemas.openxmlformats.org/drawingml/2006/main\">"
                "<p:cSld><p:spTree><p:sp><p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r>"
                "<a:t>https://example.com/repeated</a:t></a:r></a:p></p:txBody></p:sp>"
                "</p:spTree></p:cSld></p:sld>"
            ),
            "ppt/slides/slide2.xml": (
                "<p:sld xmlns:p=\"http://schemas.openxmlformats.org/presentationml/2006/main\" "
                "xmlns:a=\"http://schemas.openxmlformats.org/drawingml/2006/main\">"
                "<p:cSld><p:spTree><p:sp><p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r>"
                "<a:t>Again https://example.com/repeated</a:t></a:r></a:p></p:txBody></p:sp>"
                "</p:spTree></p:cSld></p:sld>"
            ),
        }
    )
    try:
        result = extract_urls_from_pptx(path)
        assert len(result) == 1
        assert len(result[0].occurrences) == 2
        assert {occ.slide_number for occ in result[0].occurrences} == {1, 2}
    finally:
        path.unlink(missing_ok=True)


def test_extract_pptx_metrics_are_recorded() -> None:
    path = _write_pptx_with_payload(
        {
            "ppt/slides/slide1.xml": (
                "<p:sld xmlns:p=\"http://schemas.openxmlformats.org/presentationml/2006/main\" "
                "xmlns:a=\"http://schemas.openxmlformats.org/drawingml/2006/main\">"
                "<p:cSld><p:spTree><p:sp><p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r>"
                "<a:t>https://example.com/one and https://example.com/two</a:t></a:r></a:p>"
                "</p:txBody></p:sp></p:spTree></p:cSld></p:sld>"
            ),
            "ppt/slides/_rels/slide1.xml.rels": (
                "<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">"
                "<Relationship Id='rId1' "
                "Type='http://schemas.openxmlformats.org/officeDocument/2006/"
                "relationships/hyperlink' "
                "Target='https://example.com/three'/></Relationships>"
            ),
        }
    )
    try:
        metrics = InputMetrics()
        result = extract_urls_from_pptx(path, metrics=metrics)
        assert metrics.read_time_ms >= 0.0
        assert metrics.extraction_time_ms >= 0.0
        assert metrics.urls_found >= 3
        assert metrics.urls_unique == len(result)
    finally:
        path.unlink(missing_ok=True)


def test_pptx_invalid_and_non_pptx() -> None:
    bad_file = Path("tests/.tmp_bad.pptx")
    bad_file.write_bytes(b"not a valid zip")
    try:
        with pytest.raises(InvalidPowerPointError):
            extract_urls_from_pptx(bad_file)

        missing = Path("tests/does_not_exist.pptx")
        with pytest.raises(InvalidPowerPointError):
            extract_urls_from_pptx(missing)

        plain_txt = Path("tests/.tmp_plain.txt")
        plain_txt.write_text("https://example.com/plain")
        try:
            with pytest.raises(InvalidPowerPointError):
                extract_urls_from_pptx(plain_txt)
        finally:
            plain_txt.unlink(missing_ok=True)
    finally:
        bad_file.unlink(missing_ok=True)


def test_extract_pptx_without_urls() -> None:
    path = _write_pptx_with_payload({
        "ppt/slides/slide3.xml": (
            "<p:sld xmlns:p=\"http://schemas.openxmlformats.org/presentationml/2006/main\" "
            "xmlns:a=\"http://schemas.openxmlformats.org/drawingml/2006/main\">"
            "<p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>No links here</a:t></a:r>"
            "</a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>"
        )
    })
    try:
        result = extract_urls_from_pptx(path)
        assert result == []
    finally:
        path.unlink(missing_ok=True)
