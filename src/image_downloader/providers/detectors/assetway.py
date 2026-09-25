"""Assetway detector."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

from image_downloader.providers.models import ProviderId
from image_downloader.providers.registry import ProviderDetector


class AssetwayDetector(ProviderDetector):
    provider_id = ProviderId.ASSETWAY

    def matches(self, hostname: str, path: str) -> bool:
        if not hostname:
            return False
        return hostname == "plataformaa.assetway.com.br" or hostname == "i.assetway.com.br"

    def extract_reference(self, hostname: str, path: str, query: str) -> str | None:
        parsed = urlsplit(f"https://{hostname}{path}?{query}")
        params = parse_qs(parsed.query, keep_blank_values=True)
        for key in ("assetId", "assetid"):
            if key in params and params[key]:
                return params[key][0]
        return None

    def build_details(self, hostname: str, path: str, query: str) -> dict[str, object]:
        parsed = urlsplit(f"https://{hostname}{path}?{query}")
        query_params = parse_qs(parsed.query, keep_blank_values=True)
        return {
            "known_asset_path": "assetId" in query_params or "assetid" in query_params,
            "hostname": hostname,
        }
