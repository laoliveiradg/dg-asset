"""Domain-specific exceptions for input parsing."""

from __future__ import annotations


class InputProcessingError(ValueError):
    """Base exception for input module failures."""


class InvalidPowerPointError(InputProcessingError):
    """Raised when a PPTX file is missing, invalid, or unusable."""


class InvalidTextInputError(InputProcessingError):
    """Raised when a text input cannot be processed safely."""
