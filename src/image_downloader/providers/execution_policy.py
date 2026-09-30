"""Central provider-to-execution-mode policy."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from image_downloader.providers.capabilities import ProviderExecutionMode
from image_downloader.providers.models import ProviderId

DEFAULT_PROVIDER_EXECUTION_MODES = MappingProxyType(
    {
        ProviderId.ASSETWAY: ProviderExecutionMode.AUTOMATED,
        ProviderId.SHUTTERSTOCK: ProviderExecutionMode.INTERACTIVE_REQUIRED,
        ProviderId.ENVATO: ProviderExecutionMode.UNVALIDATED,
        ProviderId.UNKNOWN: ProviderExecutionMode.UNAVAILABLE,
    }
)


class ProviderExecutionPolicy:
    """Resolve execution capability without coupling it to provider detection."""

    def __init__(
        self,
        modes: Mapping[ProviderId, ProviderExecutionMode] | None = None,
    ) -> None:
        self.modes = MappingProxyType(
            dict(DEFAULT_PROVIDER_EXECUTION_MODES if modes is None else modes)
        )

    def mode_for(self, provider: ProviderId | str) -> ProviderExecutionMode:
        try:
            resolved = provider if isinstance(provider, ProviderId) else ProviderId(provider)
        except (TypeError, ValueError):
            return ProviderExecutionMode.UNAVAILABLE
        return self.modes.get(resolved, ProviderExecutionMode.UNAVAILABLE)


DEFAULT_PROVIDER_EXECUTION_POLICY = ProviderExecutionPolicy()
