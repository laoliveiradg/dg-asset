"""Small localhost-only Chrome DevTools Protocol client."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from itertools import count
from urllib.parse import urlsplit

import websocket

from image_downloader.chrome.models import CdpError, CdpTarget


class CdpClient:
    """Use HTTP discovery and short-lived WebSockets bound to loopback only."""

    def __init__(
        self,
        port: int,
        *,
        timeout: float = 5.0,
        json_getter: Callable[[str, float], object] | None = None,
        websocket_factory: Callable[..., object] = websocket.create_connection,
    ) -> None:
        if not 1 <= port <= 65535:
            raise CdpError("Chrome DevTools reported an invalid local port.")
        self.port = port
        self.timeout = timeout
        self._json_getter = json_getter or self._get_json
        self._websocket_factory = websocket_factory
        self._command_ids = count(1)

    @property
    def http_origin(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def check_connection(self) -> dict[str, object]:
        info = self._json_getter("/json/version", self.timeout)
        if not isinstance(info, dict):
            raise CdpError("Chrome DevTools returned an invalid version response.")
        self._validate_websocket_url(info.get("webSocketDebuggerUrl"))
        return info

    def list_pages(self) -> list[CdpTarget]:
        raw_targets = self._json_getter("/json/list", self.timeout)
        if not isinstance(raw_targets, list):
            raise CdpError("Chrome DevTools returned an invalid target list.")
        pages: list[CdpTarget] = []
        for target in raw_targets:
            if not isinstance(target, dict) or target.get("type") != "page":
                continue
            target_id = target.get("id")
            if not isinstance(target_id, str):
                continue
            pages.append(
                CdpTarget(
                    target_id=target_id,
                    target_type="page",
                    url=str(target.get("url", "")),
                    title=str(target.get("title", "")),
                )
            )
        return pages

    def open_page(self, url: str) -> str:
        self.check_connection()
        result = self._browser_command("Target.createTarget", {"url": url})
        target_id = result.get("targetId") if isinstance(result, dict) else None
        if not isinstance(target_id, str) or not target_id:
            raise CdpError("Chrome DevTools did not create a page target.")
        return target_id

    def close_target(self, target_id: str) -> bool:
        result = self._browser_command("Target.closeTarget", {"targetId": target_id})
        return bool(result.get("success")) if isinstance(result, dict) else False

    def navigate(self, target_id: str, url: str) -> None:
        target = next((item for item in self.list_pages() if item.target_id == target_id), None)
        if target is None:
            raise CdpError("The managed Chrome page is no longer available.")
        raw_targets = self._json_getter("/json/list", self.timeout)
        raw_target = next(
            (
                item
                for item in raw_targets
                if isinstance(item, dict) and item.get("id") == target_id
            ),
            None,
        )
        if raw_target is None:
            raise CdpError("The managed Chrome page is no longer available.")
        websocket_url = self._validate_websocket_url(raw_target.get("webSocketDebuggerUrl"))
        self._send_command(websocket_url, "Page.navigate", {"url": url})

    def get_page(self, target_id: str) -> CdpTarget:
        target = next((item for item in self.list_pages() if item.target_id == target_id), None)
        if target is None:
            raise CdpError("The managed Chrome page is no longer available.")
        return target

    def close_browser(self) -> None:
        info = self.check_connection()
        websocket_url = self._validate_websocket_url(info.get("webSocketDebuggerUrl"))
        self._send_command(websocket_url, "Browser.close", {})

    def _browser_command(self, method: str, params: dict[str, object]) -> object:
        info = self.check_connection()
        websocket_url = self._validate_websocket_url(info.get("webSocketDebuggerUrl"))
        return self._send_command(websocket_url, method, params)

    def _send_command(
        self,
        websocket_url: str,
        method: str,
        params: dict[str, object],
    ) -> object:
        command_id = next(self._command_ids)
        try:
            connection = self._websocket_factory(
                websocket_url,
                timeout=self.timeout,
                suppress_origin=True,
                http_no_proxy=["127.0.0.1", "localhost"],
            )
        except Exception as error:
            raise CdpError("Unable to connect to the local Chrome DevTools endpoint.") from error
        try:
            connection.send(json.dumps({"id": command_id, "method": method, "params": params}))
            while True:
                response = json.loads(connection.recv())
                if response.get("id") != command_id:
                    continue
                if "error" in response:
                    raise CdpError(f"Chrome DevTools command failed: {method}.")
                return response.get("result", {})
        except CdpError:
            raise
        except Exception as error:
            raise CdpError(f"Chrome DevTools command failed: {method}.") from error
        finally:
            try:
                connection.close()
            except Exception:
                pass

    def _validate_websocket_url(self, raw_url: object) -> str:
        if not isinstance(raw_url, str):
            raise CdpError("Chrome DevTools did not provide a WebSocket endpoint.")
        parsed = urlsplit(raw_url)
        if (
            parsed.scheme != "ws"
            or parsed.hostname != "127.0.0.1"
            or parsed.port != self.port
            or not parsed.path.startswith("/devtools/")
        ):
            raise CdpError("Chrome DevTools endpoint is not restricted to local loopback.")
        return raw_url

    def _get_json(self, path: str, timeout: float) -> object:
        request = urllib.request.Request(self.http_origin + path, method="GET")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except (OSError, urllib.error.URLError, ValueError) as error:
            raise CdpError("Unable to read the local Chrome DevTools endpoint.") from error