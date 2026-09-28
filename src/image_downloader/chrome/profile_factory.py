"""Deterministic provider-isolated Chrome user-data directories."""

from __future__ import annotations

import shutil
from pathlib import Path

from image_downloader.chrome.config import SUPPORTED_PROVIDERS
from image_downloader.chrome.models import ChromeProfilePaths, UnsupportedChromeProviderError
from image_downloader.providers.models import ProviderId

PROFILE_SLUGS = {
    ProviderId.ASSETWAY: "assetway",
    ProviderId.SHUTTERSTOCK: "shutterstock",
    ProviderId.ENVATO: "envato",
}


class ChromeProfileFactory:
    """Resolve stable Chrome profiles under runtime/chrome_profiles."""

    def __init__(self, runtime_root: str | Path | None = None) -> None:
        self.runtime_root = (
            Path(runtime_root).resolve()
            if runtime_root is not None
            else Path(__file__).resolve().parents[3] / "runtime"
        )

    def paths_for(self, provider: ProviderId | str) -> ChromeProfilePaths:
        resolved = self._require_provider(provider)
        provider_root = self.runtime_root / "chrome_profiles" / PROFILE_SLUGS[resolved]
        generations = self._generations_in(provider_root)
        generation = max(generations, default=0)
        return self.paths_for_generation(resolved, generation)

    def paths_for_generation(
        self,
        provider: ProviderId | str,
        generation: int,
    ) -> ChromeProfilePaths:
        resolved = self._require_provider(provider)
        provider_root = self.runtime_root / "chrome_profiles" / PROFILE_SLUGS[resolved]
        user_data_dir = provider_root / f"profile-{generation:04d}"
        return ChromeProfilePaths(resolved, provider_root, user_data_dir, generation)

    def ensure_profile(self, provider: ProviderId | str) -> ChromeProfilePaths:
        paths = self.paths_for(provider)
        paths.user_data_dir.mkdir(parents=True, exist_ok=True)
        return paths

    def clear_provider(self, provider: ProviderId | str) -> ChromeProfilePaths:
        paths = self.paths_for(provider)
        shutil.rmtree(paths.provider_root, ignore_errors=False)
        replacement = self.paths_for(provider)
        replacement.user_data_dir.mkdir(parents=True, exist_ok=True)
        return replacement

    @staticmethod
    def _require_provider(provider: ProviderId | str) -> ProviderId:
        try:
            resolved = provider if isinstance(provider, ProviderId) else ProviderId(provider)
        except ValueError as error:
            message = "Provider has no managed Chrome profile."
            raise UnsupportedChromeProviderError(message) from error
        if resolved not in SUPPORTED_PROVIDERS:
            raise UnsupportedChromeProviderError("Provider has no managed Chrome profile.")
        return resolved

    @staticmethod
    def _generations_in(provider_root: Path) -> list[int]:
        if not provider_root.is_dir():
            return []
        generations: list[int] = []
        for entry in provider_root.iterdir():
            if not entry.is_dir() or not entry.name.startswith("profile-"):
                continue
            suffix = entry.name.removeprefix("profile-")
            if suffix.isdecimal():
                generations.append(int(suffix))
        return generations