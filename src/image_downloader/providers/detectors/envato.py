"""Envato Elements detector."""

from __future__ import annotations

import re

from image_downloader.providers.models import ProviderId
from image_downloader.providers.registry import ProviderDetector


class EnvatoDetector(ProviderDetector):
    provider_id = ProviderId.ENVATO

    def matches(self, hostname: str, path: str) -> bool:
        if not hostname:
            return False
        return hostname in {"elements.envato.com", "app.envato.com"}

    def extract_reference(self, hostname: str, path: str, query: str) -> str | None:
        last_segment = path.rstrip("/").split("/")[-1] if path else ""
        if not last_segment:
            return None
        if hostname == "app.envato.com":
            match = re.fullmatch(
                r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                last_segment,
                flags=re.IGNORECASE,
            )
            return match.group(0).lower() if match else None
        if len(last_segment) < 4:
            return None
        if not re.search(r"[A-Z0-9]", last_segment, flags=re.IGNORECASE):
            return None
        digits = re.search(r"\d", last_segment)
        letters = re.search(r"[A-Z]", last_segment, flags=re.IGNORECASE)
        if not digits or not letters:
            return None
        match = re.search(r"([A-Z0-9]{4,})$", last_segment, flags=re.IGNORECASE)
        if match:
            return match.group(1).upper()
        return None

    def build_details(self, hostname: str, path: str, query: str) -> dict[str, object]:
        return {
            "hostname": hostname,
            "path_last_segment": path.rstrip("/").split("/")[-1] if path else None,
        }
