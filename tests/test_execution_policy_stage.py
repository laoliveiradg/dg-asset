from __future__ import annotations

import os
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from image_downloader.chrome import controller as chrome_controller_module
from image_downloader.chrome.controller import ChromeSessionController
from image_downloader.providers.capabilities import ProviderExecutionMode
from image_downloader.providers.execution_policy import (
    DEFAULT_PROVIDER_EXECUTION_MODES,
    DEFAULT_PROVIDER_EXECUTION_POLICY,
    ProviderExecutionPolicy,
)
from image_downloader.providers.interactive import (
    InteractiveProviderError,
    open_interactive_provider,
)
from image_downloader.providers.models import ProviderId


@pytest.fixture(scope="session")
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize(
    ("provider", "expected"),
    [
        (ProviderId.SHUTTERSTOCK, ProviderExecutionMode.INTERACTIVE_REQUIRED),
        (ProviderId.ASSETWAY, ProviderExecutionMode.UNVALIDATED),
        (ProviderId.ENVATO, ProviderExecutionMode.UNVALIDATED),
        (ProviderId.UNKNOWN, ProviderExecutionMode.UNAVAILABLE),
        ("not-a-provider", ProviderExecutionMode.UNAVAILABLE),
    ],
)
def test_default_execution_policy_modes(provider, expected) -> None:
    assert DEFAULT_PROVIDER_EXECUTION_POLICY.mode_for(provider) == expected


def test_execution_modes_are_centralized_and_not_automated() -> None:
    assert DEFAULT_PROVIDER_EXECUTION_MODES[ProviderId.SHUTTERSTOCK] == (
        ProviderExecutionMode.INTERACTIVE_REQUIRED
    )
    assert DEFAULT_PROVIDER_EXECUTION_MODES[ProviderId.ASSETWAY] == (
        ProviderExecutionMode.UNVALIDATED
    )
    assert DEFAULT_PROVIDER_EXECUTION_MODES[ProviderId.ENVATO] == (
        ProviderExecutionMode.UNVALIDATED
    )
    assert all(
        mode != ProviderExecutionMode.AUTOMATED
        for mode in DEFAULT_PROVIDER_EXECUTION_MODES.values()
    )


def test_open_interactive_provider_preserves_query_and_fragment(monkeypatch) -> None:
    url = "https://www.shutterstock.com/image-photo/forest-123?size=large&ref=queue#details"
    opener = Mock(return_value=True)
    monkeypatch.setattr("image_downloader.providers.interactive.webbrowser.open", opener)

    opened = open_interactive_provider(ProviderId.SHUTTERSTOCK, url)

    assert opened is True
    opener.assert_called_once_with(url)


def test_open_interactive_provider_requires_interactive_policy(monkeypatch) -> None:
    opener = Mock(return_value=True)
    monkeypatch.setattr("image_downloader.providers.interactive.webbrowser.open", opener)

    with pytest.raises(InteractiveProviderError):
        open_interactive_provider(ProviderId.ASSETWAY, "https://example.com/item")

    opener.assert_not_called()


def test_open_interactive_provider_wraps_default_browser_failure(monkeypatch) -> None:
    def fail_to_open(url: str) -> bool:
        raise OSError("default browser unavailable")

    monkeypatch.setattr("image_downloader.providers.interactive.webbrowser.open", fail_to_open)

    with pytest.raises(InteractiveProviderError, match="default browser could not open"):
        open_interactive_provider(
            ProviderId.SHUTTERSTOCK,
            "https://www.shutterstock.com/item/123",
        )


def test_chrome_controller_does_not_create_runtime_for_interactive_provider(
    qt_app, monkeypatch
) -> None:
    runtime_factory = Mock(side_effect=AssertionError("ChromeRuntime must remain unused"))
    monkeypatch.setattr(chrome_controller_module, "ChromeRuntime", runtime_factory)
    controller = ChromeSessionController(execution_policy=ProviderExecutionPolicy())

    status = controller.status(ProviderId.SHUTTERSTOCK)
    opened = controller.open_provider(ProviderId.SHUTTERSTOCK)
    cleared = controller.clear_provider(ProviderId.SHUTTERSTOCK)
    controller.refresh_statuses()
    controller.shutdown()
    controller._shutdown_thread.join(timeout=2.0)

    assert status.reason == "provider_not_managed_by_chrome"
    assert not status.chrome_running
    assert opened is False
    assert cleared is False
    runtime_factory.assert_not_called()