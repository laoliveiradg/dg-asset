"""LEGACY INACTIVE: QtWebEngine profile factory retained for rollback only."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWebEngineCore import QWebEngineProfile

from image_downloader.browser.session_models import (
    ProfilePaths,
    require_supported_provider,
)
from image_downloader.providers.models import ProviderId

PROFILE_SLUGS = {
    ProviderId.ASSETWAY: "assetway",
    ProviderId.SHUTTERSTOCK: "shutterstock",
    ProviderId.ENVATO: "envato",
}
SUPPORTED_PROVIDERS = tuple(PROFILE_SLUGS)


class ProfileFactory:
    """Resolve stable paths and create persistent profiles on the Qt UI thread."""

    def __init__(self, runtime_root: str | Path | None = None) -> None:
        self.runtime_root = (
            Path(runtime_root).resolve()
            if runtime_root is not None
            else Path(__file__).resolve().parents[3] / "runtime"
        )

    def paths_for(self, provider: ProviderId | str) -> ProfilePaths:
        resolved = require_supported_provider(provider)
        root = self.runtime_root / "browser_profiles" / PROFILE_SLUGS[resolved]
        generations = self._generations_in(root)
        generation = max(generations, default=0)
        return self.paths_for_generation(resolved, generation)

    def paths_for_generation(self, provider: ProviderId | str, generation: int) -> ProfilePaths:
        resolved = require_supported_provider(provider)
        root = self.runtime_root / "browser_profiles" / PROFILE_SLUGS[resolved]
        generation_root = root / f"profile-{generation:04d}"
        return ProfilePaths(
            provider=resolved,
            root=root,
            generation_root=generation_root,
            persistent_storage=generation_root / "storage",
            cache=generation_root / "cache",
            generation=generation,
        )

    def next_generation(self, paths: ProfilePaths) -> ProfilePaths:
        return self.paths_for_generation(paths.provider, paths.generation + 1)

    def stale_generation_roots(self, paths: ProfilePaths) -> tuple[Path, ...]:
        generations = self._generations_in(paths.root)
        return tuple(
            paths.root / f"profile-{generation:04d}"
            for generation in generations
            if generation != paths.generation
        )

    def create_profile(
        self,
        paths: ProfilePaths,
        parent=None,
    ) -> QWebEngineProfile:
        paths.persistent_storage.mkdir(parents=True, exist_ok=True)
        paths.cache.mkdir(parents=True, exist_ok=True)
        slug = PROFILE_SLUGS[paths.provider]
        profile = QWebEngineProfile(
            f"image-downloader-{slug}-{paths.generation:04d}",
            parent,
        )
        profile.setPersistentStoragePath(str(paths.persistent_storage))
        profile.setCachePath(str(paths.cache))
        profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.DiskHttpCache)
        profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies
        )
        return profile

    @staticmethod
    def _generations_in(root: Path) -> list[int]:
        generations: list[int] = []
        if not root.is_dir():
            return generations
        for entry in root.iterdir():
            if not entry.is_dir() or not entry.name.startswith("profile-"):
                continue
            suffix = entry.name.removeprefix("profile-")
            if suffix.isdecimal():
                generations.append(int(suffix))
        return generations