"""PowerPoint OOXML extraction without PowerPoint installed."""

from __future__ import annotations

import logging
import re
import zipfile
from collections.abc import Iterable
from pathlib import Path
from xml.etree import ElementTree

from image_downloader.input.exceptions import InvalidPowerPointError
from image_downloader.input.models import InputMetrics, SourceType, UrlOccurrence
from image_downloader.input.normalization import normalize_url
from image_downloader.input.text_extractor import consolidate_urls, extract_urls_from_text

logger = logging.getLogger(__name__)


def _extract_slide_number(source_name: str) -> int | None:
    match = re.search(r"(?:slide|notesSlide)(\d+)", source_name, flags=re.IGNORECASE)
    if not match:
        return None
    return int(match.group(1))


def _extract_text_from_element(element: ElementTree.Element) -> str:
    fragments: list[str] = []
    for node in element.iter():
        tag = node.tag.rsplit("}", 1)[-1]
        if tag == "t" and node.text:
            fragments.append(node.text)
    return " ".join(fragments)


def _relationships_targets(xml_text: str) -> Iterable[str]:
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return []

    targets: list[str] = []
    for node in root.iter():
        target = node.attrib.get("Target")
        if target and (target.startswith("http://") or target.startswith("https://")):
            targets.append(target)
    return targets


def _extract_xml_urls(
    xml_text: str,
    *,
    source_type: SourceType,
    source_name: str,
    file_name: str,
) -> list[UrlOccurrence]:
    if not xml_text.strip():
        return []

    text_chunks: list[str] = []
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return []

    for node in root.iter():
        tag = node.tag.rsplit("}", 1)[-1]
        if tag == "t" and node.text:
            text_chunks.append(node.text)

    occurrences: list[UrlOccurrence] = []
    slide_number = _extract_slide_number(source_name)
    for piece in text_chunks:
        occurrences.extend(
            extract_urls_from_text(
                piece,
                source_type=source_type,
                source_name=source_name,
                file_name=file_name,
                slide_number=slide_number,
                location=source_name,
            )
        )

    for target in _relationships_targets(xml_text):
        normalized = normalize_url(target)
        if not normalized:
            continue
        occurrences.append(
            UrlOccurrence(
                original_url=target,
                normalized_url=normalized,
                source_type=SourceType.relationship,
                source_name=source_name,
                file_name=file_name,
                slide_number=slide_number,
                location=source_name,
                context="relationship target",
            )
        )

    return occurrences


def extract_urls_from_pptx(
    file_path: str | Path,
    *,
    metrics: InputMetrics | None = None,
) -> list:
    """Read a PPTX package directly from OOXML and produce deduplicated unique URLs."""
    path = Path(file_path)

    read_start = 0.0
    extraction_start = 0.0
    if metrics is not None:
        import time

        read_start = time.perf_counter()

    if not path.exists() or path.is_dir():
        raise InvalidPowerPointError(f"PowerPoint file does not exist or is not a file: {path}")
    if path.stat().st_size == 0:
        raise InvalidPowerPointError(f"PowerPoint file is empty: {path}")
    if path.suffix.lower() != ".pptx":
        raise InvalidPowerPointError(f"Expected a .pptx file but received: {path.name}")

    try:
        with zipfile.ZipFile(path, "r") as archive:
            names = archive.namelist()
            if not names:
                raise InvalidPowerPointError(f"PowerPoint package is empty: {path}")

            if metrics is not None:
                metrics.read_time_ms = (time.perf_counter() - read_start) * 1000
                extraction_start = time.perf_counter()

            occurrences: list[UrlOccurrence] = []
            for entry_name in sorted(names):
                if not entry_name.startswith("ppt/") or not entry_name.endswith(".xml"):
                    continue

                if "/_rels/" in entry_name or entry_name.endswith(".rels"):
                    continue

                kind = SourceType.unknown
                if "comments" in entry_name:
                    kind = SourceType.comment
                elif "notesSlides" in entry_name:
                    kind = SourceType.note
                elif "slides" in entry_name:
                    kind = SourceType.slide_text
                elif "slideMasters" in entry_name:
                    kind = SourceType.relationship
                else:
                    kind = SourceType.unknown

                try:
                    xml_text = archive.read(entry_name).decode("utf-8", errors="strict")
                except UnicodeDecodeError:
                    continue

                occurrences.extend(
                    _extract_xml_urls(
                        xml_text,
                        source_type=kind,
                        source_name=entry_name,
                        file_name=path.name,
                    )
                )

            rel_names = [
                name for name in names if name.startswith("ppt/") and name.endswith(".xml.rels")
            ]
            for relation_name in rel_names:
                try:
                    relation_xml = archive.read(relation_name).decode("utf-8", errors="strict")
                except UnicodeDecodeError:
                    continue

                for target in _relationships_targets(relation_xml):
                    normalized = normalize_url(target)
                    if not normalized:
                        continue
                    occurrences.append(
                        UrlOccurrence(
                            original_url=target,
                            normalized_url=normalized,
                            source_type=SourceType.hyperlink,
                            source_name=relation_name,
                            file_name=path.name,
                            slide_number=_extract_slide_number(relation_name),
                            location=relation_name,
                            context="hyperlink relationship",
                        )
                    )

            unique = consolidate_urls(occurrences)
            if metrics is not None:
                metrics.extraction_time_ms = (time.perf_counter() - extraction_start) * 1000
                metrics.urls_found = len(occurrences)
                metrics.urls_unique = len(unique)
            return unique
    except zipfile.BadZipFile as exc:
        raise InvalidPowerPointError(
            f"PowerPoint package is corrupted or not a valid ZIP: {path}"
        ) from exc
