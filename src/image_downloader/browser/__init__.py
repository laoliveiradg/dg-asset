"""LEGACY INACTIVE: retained QtWebEngine sessions for rollback only."""

from image_downloader.browser.session_manager import SessionManager
from image_downloader.browser.session_models import SessionState, SessionStatus

__all__ = ["SessionManager", "SessionState", "SessionStatus"]
