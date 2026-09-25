"""Input handling package."""

from image_downloader.input.exceptions import InputProcessingError, InvalidPowerPointError
from image_downloader.input.models import InputMetrics, SourceType, UrlOccurrence, UrlRecord
from image_downloader.input.normalization import normalize_url
from image_downloader.input.pptx_reader import extract_urls_from_pptx
from image_downloader.input.text_extractor import consolidate_urls, extract_urls_from_text

__all__ = [
    "InputMetrics",
    "InputProcessingError",
    "InvalidPowerPointError",
    "SourceType",
    "UrlOccurrence",
    "UrlRecord",
    "consolidate_urls",
    "extract_urls_from_pptx",
    "extract_urls_from_text",
    "normalize_url",
]
