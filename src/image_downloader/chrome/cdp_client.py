"""Small localhost-only Chrome DevTools Protocol client."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from itertools import count
from pathlib import Path
from urllib.parse import urlsplit

import websocket

from image_downloader.chrome.models import CdpError, CdpTarget


@dataclass(frozen=True, slots=True)
class CdpDownloadEvent:
    guid: str
    suggested_filename: str
    received_bytes: int
    total_bytes: int
    frame_id: str | None


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

    def navigate(self, target_id: str, url: str) -> str | None:
        websocket_url = self._page_websocket_url(target_id)
        result = self._send_command(websocket_url, "Page.navigate", {"url": url})
        if not isinstance(result, dict):
            raise CdpError("Chrome DevTools returned an invalid navigation result.")
        if result.get("errorText"):
            raise CdpError("Chrome DevTools could not navigate the managed page.")
        frame_id = result.get("frameId")
        return frame_id if isinstance(frame_id, str) else None

    def evaluate_page(self, target_id: str, expression: str) -> object:
        """Evaluate a read-only or user-approved expression in an owned page target."""

        websocket_url = self._page_websocket_url(target_id)
        response = self._send_command(
            websocket_url,
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
        )
        if not isinstance(response, dict) or "exceptionDetails" in response:
            raise CdpError("Chrome DevTools could not evaluate the managed page expression.")
        remote_object = response.get("result")
        if not isinstance(remote_object, dict):
            raise CdpError("Chrome DevTools returned an invalid page evaluation result.")
        return remote_object.get("value")

    def frame_ids(self, target_id: str) -> tuple[str, ...]:
        websocket_url = self._page_websocket_url(target_id)
        result = self._send_command(websocket_url, "Page.getFrameTree", {})
        frame_tree = result.get("frameTree") if isinstance(result, dict) else None
        if not isinstance(frame_tree, dict):
            raise CdpError("Chrome DevTools returned an invalid frame tree.")
        frame_ids: list[str] = []

        def collect(node: object) -> None:
            if not isinstance(node, dict):
                return
            frame = node.get("frame")
            frame_id = frame.get("id") if isinstance(frame, dict) else None
            if isinstance(frame_id, str) and frame_id:
                frame_ids.append(frame_id)
            children = node.get("childFrames")
            if isinstance(children, list):
                for child in children:
                    collect(child)

        collect(frame_tree)
        return tuple(frame_ids)

    def evaluate_frame(self, target_id: str, frame_id: str, expression: str) -> object:
        websocket_url = self._page_websocket_url(target_id)
        world = self._send_command(
            websocket_url,
            "Page.createIsolatedWorld",
            {
                "frameId": frame_id,
                "worldName": "image-downloader-assetway",
                "grantUniveralAccess": False,
            },
        )
        context_id = world.get("executionContextId") if isinstance(world, dict) else None
        if not isinstance(context_id, int):
            raise CdpError("Chrome DevTools could not create an accessible frame context.")
        response = self._send_command(
            websocket_url,
            "Runtime.evaluate",
            {
                "expression": expression,
                "contextId": context_id,
                "returnByValue": True,
                "awaitPromise": True,
            },
        )
        if not isinstance(response, dict) or "exceptionDetails" in response:
            raise CdpError("Chrome DevTools could not inspect the managed frame.")
        remote_object = response.get("result")
        if not isinstance(remote_object, dict):
            raise CdpError("Chrome DevTools returned an invalid frame evaluation result.")
        return remote_object.get("value")

    def click_page_point(self, target_id: str, x: float, y: float) -> None:
        websocket_url = self._page_websocket_url(target_id)
        common = {"x": x, "y": y, "button": "left", "clickCount": 1}
        self._send_command(
            websocket_url,
            "Input.dispatchMouseEvent",
            {"type": "mouseMoved", "x": x, "y": y},
        )
        self._send_command(
            websocket_url,
            "Input.dispatchMouseEvent",
            {"type": "mousePressed", **common},
        )
        self._send_command(
            websocket_url,
            "Input.dispatchMouseEvent",
            {"type": "mouseReleased", **common},
        )

    def _page_websocket_url(self, target_id: str) -> str:
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
        return self._validate_websocket_url(raw_target.get("webSocketDebuggerUrl"))

    def get_page(self, target_id: str) -> CdpTarget:
        target = next((item for item in self.list_pages() if item.target_id == target_id), None)
        if target is None:
            raise CdpError("The managed Chrome page is no longer available.")
        return target

    def main_frame_id(self, target_id: str) -> str:
        websocket_url = self._page_websocket_url(target_id)
        result = self._send_command(websocket_url, "Page.getFrameTree", {})
        frame_tree = result.get("frameTree") if isinstance(result, dict) else None
        frame = frame_tree.get("frame") if isinstance(frame_tree, dict) else None
        frame_id = frame.get("id") if isinstance(frame, dict) else None
        if not isinstance(frame_id, str) or not frame_id:
            raise CdpError("Chrome DevTools did not identify the managed page frame.")
        return frame_id

    def close_browser(self) -> None:
        info = self.check_connection()
        websocket_url = self._validate_websocket_url(info.get("webSocketDebuggerUrl"))
        self._send_command(websocket_url, "Browser.close", {})

    def monitor_downloads(self, download_path: str | Path) -> CdpDownloadMonitor:
        return CdpDownloadMonitor(self, Path(download_path))

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


class CdpDownloadMonitor:
    """Observe one Chrome download through Browser domain events on local CDP."""

    def __init__(self, client: CdpClient, download_path: Path) -> None:
        self.client = client
        self.download_path = download_path
        self._connection = None
        self._active_guid: str | None = None
        self._suggested_filename: str | None = None
        self._frame_id: str | None = None

    def __enter__(self) -> CdpDownloadMonitor:
        if not self.download_path.is_absolute():
            raise CdpError("Chrome download directory must be an absolute local path.")
        info = self.client.check_connection()
        websocket_url = self.client._validate_websocket_url(
            info.get("webSocketDebuggerUrl")
        )
        try:
            self._connection = self.client._websocket_factory(
                websocket_url,
                timeout=0.5,
                suppress_origin=True,
                http_no_proxy=["127.0.0.1", "localhost"],
            )
        except Exception as error:
            raise CdpError("Unable to subscribe to local Chrome download events.") from error
        try:
            self._send_command(
                "Browser.setDownloadBehavior",
                {
                    "behavior": "allow",
                    "downloadPath": str(self.download_path),
                    "eventsEnabled": True,
                },
            )
        except Exception:
            self._close_connection()
            raise
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        try:
            if self._connection is not None:
                self._send_command(
                    "Browser.setDownloadBehavior",
                    {"behavior": "default", "eventsEnabled": False},
                    timeout=2.0,
                )
        finally:
            self._close_connection()

    def wait_for_completion(self, timeout: float) -> CdpDownloadEvent:
        if self._active_guid is None and not self.wait_for_start(timeout):
            raise CdpError("Chrome download did not begin before the timeout.")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            message = self._receive_message(deadline)
            if message is None:
                continue
            if message.get("method") == "Browser.downloadWillBegin":
                parameters = message.get("params")
                self._set_download_started(parameters)
                continue
            if message.get("method") != "Browser.downloadProgress":
                continue
            parameters = message.get("params")
            if not isinstance(parameters, dict) or parameters.get("guid") != self._active_guid:
                continue
            state = parameters.get("state")
            if state == "canceled":
                raise CdpError("Chrome canceled the selected item download.")
            if state != "completed":
                continue
            received_bytes = parameters.get("receivedBytes")
            total_bytes = parameters.get("totalBytes")
            if not isinstance(received_bytes, (int, float)) or not isinstance(
                total_bytes, (int, float)
            ):
                raise CdpError("Chrome returned incomplete download progress metadata.")
            if self._active_guid is None or self._suggested_filename is None:
                raise CdpError("Chrome completed a download without a matching start event.")
            return CdpDownloadEvent(
                guid=self._active_guid,
                suggested_filename=self._suggested_filename,
                received_bytes=int(received_bytes),
                total_bytes=int(total_bytes),
                frame_id=self._frame_id,
            )
        raise CdpError("Chrome download did not complete before the timeout.")

    def wait_for_start(self, timeout: float, *, expected_frame_id: str | None = None) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            message = self._receive_message(deadline)
            if message is None:
                continue
            if message.get("method") == "Browser.downloadWillBegin":
                parameters = message.get("params")
                if (
                    expected_frame_id is not None
                    and isinstance(parameters, dict)
                    and parameters.get("frameId") != expected_frame_id
                ):
                    continue
                self._set_download_started(parameters)
                return True
        return False

    def _set_download_started(self, parameters: object) -> None:
        if not isinstance(parameters, dict):
            raise CdpError("Chrome reported invalid download start metadata.")
        guid = parameters.get("guid")
        filename = parameters.get("suggestedFilename")
        frame_id = parameters.get("frameId")
        if not isinstance(guid, str) or not isinstance(filename, str) or not filename:
            raise CdpError("Chrome reported an unverifiable download filename.")
        if self._active_guid is not None:
            raise CdpError("More than one download started for the selected item.")
        self._active_guid = guid
        self._suggested_filename = filename
        self._frame_id = frame_id if isinstance(frame_id, str) else None

    def _send_command(
        self,
        method: str,
        params: dict[str, object],
        *,
        timeout: float | None = None,
    ) -> None:
        if self._connection is None:
            raise CdpError("Chrome download event connection is not open.")
        command_id = next(self.client._command_ids)
        try:
            self._connection.send(
                json.dumps({"id": command_id, "method": method, "params": params})
            )
            deadline = time.monotonic() + (timeout or self.client.timeout)
            while time.monotonic() < deadline:
                response = self._receive_message(deadline)
                if response is None or response.get("id") != command_id:
                    continue
                if "error" in response:
                    raise CdpError(f"Chrome DevTools command failed: {method}.")
                return
            raise CdpError(f"Chrome DevTools command timed out: {method}.")
        except CdpError:
            raise
        except Exception as error:
            raise CdpError(f"Chrome DevTools command failed: {method}.") from error

    def _receive_message(self, deadline: float) -> dict[str, object] | None:
        if self._connection is None:
            raise CdpError("Chrome download event connection is not open.")
        while time.monotonic() < deadline:
            try:
                raw_message = self._connection.recv()
            except websocket.WebSocketTimeoutException:
                return None
            except Exception as error:
                raise CdpError("Chrome download event stream ended unexpectedly.") from error
            try:
                message = json.loads(raw_message)
            except (TypeError, ValueError):
                continue
            if isinstance(message, dict):
                return message
        return None

    def _close_connection(self) -> None:
        connection, self._connection = self._connection, None
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass
