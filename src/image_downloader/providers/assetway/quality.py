"""Fail-closed selection of an explicitly identified highest-quality option."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Protocol

from image_downloader.providers.assetway.errors import quality_unverified


class LabeledControl(Protocol):
    index: int
    display_label: str


@dataclass(frozen=True, slots=True)
class QualitySelection:
    control: LabeledControl
    quality_label: str
    source_format: str | None
    rank: int


def choose_best_quality(
    controls: tuple[LabeledControl, ...] | list[LabeledControl],
) -> QualitySelection:
    candidates: list[QualitySelection] = []
    for control in controls:
        evidence = getattr(control, "semantic_label", control.display_label)
        label = _normalize(evidence)
        if not label or any(
            word in label
            for word in (
                "preview",
                "thumbnail",
                "thumb",
                "miniatura",
                "watermark",
                "marca d agua",
                "low",
                "small",
                "sample",
                "baixa resolucao",
            )
        ):
            continue
        source_format = _source_format(label)
        dimension_score = 0
        if dimensions := re.search(r"\b(\d{3,6})\s*[x×]\s*(\d{3,6})\b", label):
            pixels = int(dimensions.group(1)) * int(dimensions.group(2))
            dimension_score = min(pixels // 1_000_000, 99)
        if re.search(r"\b(original|source|fonte)\b", label):
            rank = 750 if source_format in {"EPS", "AI", "SVG"} else 600 + dimension_score
        elif source_format in {"EPS", "AI", "SVG"}:
            rank = 500
        elif re.search(
            r"\b(full resolution|full size|resolucao completa|tamanho original)\b",
            label,
        ):
            rank = 300
        elif re.search(r"\b(high resolution|high res|alta resolucao|high|alta)\b", label):
            rank = 250 + dimension_score
        elif dimension_score:
            rank = 200 + dimension_score
        else:
            continue
        candidates.append(
            QualitySelection(
                control,
                control.display_label.strip() or str(evidence).strip(),
                source_format,
                rank,
            )
        )
    if not candidates:
        raise quality_unverified()
    return max(candidates, key=lambda candidate: candidate.rank)


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _source_format(label: str) -> str | None:
    match = re.search(r"\b(eps|ai|svg|tiff|tif|png|jpe?g|webp|gif|bmp)\b", label)
    if match is None:
        return None
    source_format = match.group(1).upper()
    return "JPEG" if source_format in {"JPG", "JPEG"} else source_format
