"""User-initiated navigation through the operating system's default browser."""

from __future__ import annotations

import webbrowser
from collections.abc import Callable
from urllib.parse import urlsplit

from image_downloader.providers.capabilities import ProviderExecutionMode
from image_downloader.providers.execution_policy import (
    DEFAULT_PROVIDER_EXECUTION_POLICY,
    ProviderExecutionPolicy,
)
from image_downloader.providers.models import ProviderId


class InteractiveProviderError(RuntimeError):
    """Raised when a URL cannot be opened through an interactive provider flow."""


def open_interactive_provider(
    provider: ProviderId | str,
    url: str,
    *,
    execution_policy: ProviderExecutionPolicy = DEFAULT_PROVIDER_EXECUTION_POLICY,
    browser_opener: Callable[[str], bool] | None = None,
) -> bool:
    """Open an item URL in the default browser without controlling its profile."""

    if execution_policy.mode_for(provider) != ProviderExecutionMode.INTERACTIVE_REQUIRED:
        raise InteractiveProviderError("Provider is not configured for interactive navigation.")
    parsed = urlsplit(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise InteractiveProviderError("Interactive navigation requires an absolute HTTP URL.")
    opener = browser_opener or webbrowser.open
    try:
        return opener(url)
    except (OSError, webbrowser.Error) as error:
        raise InteractiveProviderError("The default browser could not open the URL.") from error