"""Execution modes supported by individual providers."""

from enum import StrEnum


class ProviderExecutionMode(StrEnum):
    AUTOMATED = "AUTOMATED"
    INTERACTIVE_REQUIRED = "INTERACTIVE_REQUIRED"
    UNVALIDATED = "UNVALIDATED"
    UNAVAILABLE = "UNAVAILABLE"