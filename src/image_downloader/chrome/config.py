"""Static provider entry points for manual, interactive browser sessions."""

from image_downloader.providers.models import ProviderId

PROVIDER_START_URLS = {
    ProviderId.ASSETWAY: "https://plataformaa.assetway.com.br/",
    ProviderId.SHUTTERSTOCK: "https://www.shutterstock.com/",
    ProviderId.ENVATO: "https://elements.envato.com/",
}

PROVIDER_NAMES = {
    ProviderId.ASSETWAY: "Assetway",
    ProviderId.SHUTTERSTOCK: "Shutterstock",
    ProviderId.ENVATO: "Envato Elements",
}

SUPPORTED_PROVIDERS = tuple(PROVIDER_START_URLS)