"""Concrete provider detectors."""

from image_downloader.providers.detectors.assetway import AssetwayDetector
from image_downloader.providers.detectors.envato import EnvatoDetector
from image_downloader.providers.detectors.shutterstock import ShutterstockDetector

ASSETWAY_DETECTOR = AssetwayDetector()
SHUTTERSTOCK_DETECTOR = ShutterstockDetector()
ENVATO_DETECTOR = EnvatoDetector()

__all__ = [
    "ASSETWAY_DETECTOR",
    "ENVATO_DETECTOR",
    "SHUTTERSTOCK_DETECTOR",
]
