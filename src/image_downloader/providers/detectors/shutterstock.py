"""Shutterstock detector."""

from __future__ import annotations

import re

from image_downloader.providers.models import ProviderId
from image_downloader.providers.registry import ProviderDetector


class ShutterstockDetector(ProviderDetector):
    provider_id = ProviderId.SHUTTERSTOCK

    def matches(self, hostname: str, path: str) -> bool:
        if not hostname:
            return False
        if hostname == "shutterstock.com" or hostname == "www.shutterstock.com":
            return True
        return False

    def extract_reference(self, hostname: str, path: str, query: str) -> str | None:
        pattern = (
            r"(?:image-photo|image-vector|image-illustration)"
            r"[^0-9]*(\d+)"
        )
        match = re.search(pattern, path, flags=re.IGNORECASE)
        if match:
            return match.group(1)
        return None

    def build_details(self, hostname: str, path: str, query: str) -> dict[str, object]:
        return {
            "known_path_patterns": any(
                token in path.lower()
                for token in (
                    "/image-photo/",
                    "/image-vector/",
                    "/image-illustration/",
                )
            ),
            "hostname": hostname,
        }
