from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from image_downloader.chrome.models import (
    CdpTarget,
    ChromeMode,
    ChromeProfileInUseError,
    ChromeStartupError,
)
from image_downloader.downloads.models import DownloadStatus
from image_downloader.input.models import SourceType, UrlOccurrence, UrlRecord
from image_downloader.providers.assetway.downloader import AssetwayDownloader
from image_downloader.providers.assetway.errors import AssetwayDownloadError
from image_downloader.providers.assetway.page_model import (
    PAGE_INSPECTION_EXPRESSION,
    AssetwayPageModel,
    AssetwayPageSnapshot,
    PageControl,
)
from image_downloader.providers.assetway.quality import choose_best_quality
from image_downloader.providers.execution_policy import DEFAULT_PROVIDER_EXECUTION_POLICY
from image_downloader.providers.models import ProviderId
from image_downloader.queue.manager import QueueManager
from image_downloader.queue.models import QueueState

ASSET_URL = "https://plataformaa.assetway.com.br/p/acervo/search?modal=asset&assetId=7788"
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"asset-data"


def make_item(url: str = ASSET_URL):
    occurrence = UrlOccurrence(
        original_url=url,
        normalized_url=url,
        source_type=SourceType.pasted_text,
    )
    return QueueManager().add_records([UrlRecord.from_occurrences([occurrence])])[0]


def make_snapshot(*, controls=None, login_required=False):
    if controls is None:
        controls = [
            {
                "index": 0,
                "tag": "button",
                "role": "",
                "text": "Baixar",
                "ariaLabel": "",
                "title": "",
                "popup": "",
                "context": "dialog",
                "disabled": False,
            },
            {
                "index": 1,
                "tag": "button",
                "role": "option",
                "text": "Original PNG",
                "ariaLabel": "",
                "title": "",
                "popup": "",
                "context": "dialog",
                "disabled": False,
            },
        ]
    return {
        "readyState": "complete",
        "hostname": "plataformaa.assetway.com.br",
        "pathname": "/p/acervo/search",
        "bodyTextLength": 100,
        "elementCount": 20,
        "buttonCount": 2,
        "linkCount": 1,
        "shadowRootCount": 0,
        "bodyReady": True,
        "loginRequired": login_required,
        "controls": controls,
    }


class FakeDownloadMonitor:
    def __init__(
        self,
        directory: Path,
        *,
        filename: str = "Official asset.png",
        content: bytes = PNG_BYTES,
        begin: bool = True,
        write_file: bool = True,
        temporary: bool = False,
        reported_total_bytes: int | None = None,
    ) -> None:
        self.directory = directory
        self.filename = filename
        self.content = content
        self.begin = begin
        self.write_file = write_file
        self.temporary = temporary
        self.reported_total_bytes = reported_total_bytes
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.closed = True

    def wait_for_start(self, timeout: float, *, expected_frame_id: str | None = None) -> bool:
        return self.begin

    def wait_for_completion(self, timeout: float):
        safe_filename = AssetwayDownloader._sanitize_filename(self.filename)
        if self.write_file:
            destination = self.directory / safe_filename
            if self.temporary:
                destination = destination.with_name(destination.name + ".crdownload")
            destination.write_bytes(self.content)
        return SimpleNamespace(
            guid="download-guid",
            suggested_filename=self.filename,
            received_bytes=len(self.content),
            total_bytes=(
                len(self.content)
                if self.reported_total_bytes is None
                else self.reported_total_bytes
            ),
        )


class FakeCdp:
    def __init__(self, runtime_root: Path, *, snapshot=None, monitor_options=None) -> None:
        self.current_url = "https://plataformaa.assetway.com.br/"
        self.snapshot = snapshot or make_snapshot()
        self.monitor_options = monitor_options or {}
        self.runtime_root = runtime_root
        self.navigated_urls: list[str] = []
        self.clicked_expressions: list[str] = []
        self.monitor = None

    def check_connection(self):
        return {"webSocketDebuggerUrl": "ws://127.0.0.1/devtools/browser/test"}

    def navigate(self, target_id: str, url: str) -> None:
        self.navigated_urls.append(url)
        self.current_url = url

    def list_pages(self) -> list[CdpTarget]:
        return [self.get_page("asset-page")]

    def open_page(self, url: str) -> str:
        self.current_url = url
        return "asset-page"

    def get_page(self, target_id: str) -> CdpTarget:
        return CdpTarget(target_id, "page", self.current_url, "Asset")

    def main_frame_id(self, target_id: str) -> str:
        return "main-frame"

    def evaluate_page(self, target_id: str, expression: str):
        if expression == PAGE_INSPECTION_EXPRESSION:
            return self.snapshot
        self.clicked_expressions.append(expression)
        return True

    def monitor_downloads(self, directory: Path):
        self.monitor = FakeDownloadMonitor(directory, **self.monitor_options)
        return self.monitor


class FakeRuntime:
    def __init__(self, runtime_root: Path, cdp: FakeCdp) -> None:
        self.cdp = cdp
        self.process_manager = SimpleNamespace(
            profile_factory=SimpleNamespace(runtime_root=runtime_root),
            is_running=lambda provider: True,
            managed_record=lambda provider: SimpleNamespace(
                target_id="asset-page",
                mode=ChromeMode.BACKGROUND_HEADED,
            ),
        )

    def cdp_client_for(self, provider):
        assert provider == ProviderId.ASSETWAY
        return self.cdp

    def open_provider(self, provider, *, mode=ChromeMode.INTERACTIVE):
        raise AssertionError("the test session is already managed")


def build_downloader(tmp_path, *, snapshot=None, monitor_options=None, **options):
    runtime_root = tmp_path / "runtime"
    cdp = FakeCdp(runtime_root, snapshot=snapshot, monitor_options=monitor_options)
    runtime = FakeRuntime(runtime_root, cdp)
    queue_manager = QueueManager()
    item = queue_manager.add_records(
        [
            UrlRecord.from_occurrences(
                [
                    UrlOccurrence(
                        original_url=ASSET_URL,
                        normalized_url=ASSET_URL,
                        source_type=SourceType.pasted_text,
                    )
                ]
            )
        ]
    )[0]
    downloader = AssetwayDownloader(
        runtime,
        poll_interval=0.001,
        page_timeout=0.5,
        download_timeout=0.02,
        **options,
    )
    return downloader, queue_manager, item, cdp, runtime_root


@pytest.mark.parametrize(
    ("url", "provider", "state"),
    [
        (
            "https://www.shutterstock.com/image-photo/forest-123456789",
            ProviderId.SHUTTERSTOCK,
            QueueState.READY,
        ),
        (
            "https://elements.envato.com/example-item-ABC1234",
            ProviderId.ENVATO,
            QueueState.READY,
        ),
        ("https://example.com/unknown", ProviderId.UNKNOWN, QueueState.BLOCKED),
    ],
)
def test_downloader_rejects_non_assetway_providers_without_queue_mutation(
    tmp_path, url, provider, state
) -> None:
    occurrence = UrlOccurrence(url, url, SourceType.pasted_text)
    queue = QueueManager()
    item = queue.add_records([UrlRecord.from_occurrences([occurrence])])[0]
    downloader, _, _, _, _ = build_downloader(tmp_path)

    with pytest.raises(AssetwayDownloadError, match="somente itens Assetway"):
        downloader.download_item(queue, item.item_id)

    assert queue.get_item(item.item_id).state == state


def test_assetway_success_uses_exact_item_url_and_queue_transitions(tmp_path, caplog) -> None:
    downloader, queue, item, cdp, runtime_root = build_downloader(tmp_path)
    phases: list[str] = []

    result = downloader.download_item(queue, item.item_id, phases.append)

    saved_item = queue.get_item(item.item_id)
    assert result.status == DownloadStatus.COMPLETED
    assert result.file_name == "Official asset.png"
    assert result.extension == ".png"
    assert result.source_format == "PNG"
    assert result.quality_label == "Original PNG"
    assert result.bytes_received == len(PNG_BYTES)
    assert result.file_path is not None and result.file_path.is_file()
    assert result.file_path.is_relative_to(runtime_root / "downloads")
    assert cdp.navigated_urls == [item.normalized_url]
    assert saved_item.state == QueueState.COMPLETED
    assert saved_item.attempt_count == 1
    assert DEFAULT_PROVIDER_EXECUTION_POLICY.mode_for(ProviderId.ASSETWAY) == "AUTOMATED"
    assert item.normalized_url not in caplog.text
    assert phases == [
        "Preparando ativo...",
        "Abrindo ativo...",
        "Localizando download...",
        "Selecionando qualidade...",
        "Baixando...",
        "Validando...",
        "Concluído",
    ]
    assert not result.file_path.is_relative_to(Path.home() / "Downloads")


def test_login_redirect_marks_failed_and_retryable(tmp_path) -> None:
    downloader, queue, item, _, _ = build_downloader(
        tmp_path,
        snapshot=make_snapshot(login_required=True),
    )

    result = downloader.download_item(queue, item.item_id)

    failed = queue.get_item(item.item_id)
    assert result.status == DownloadStatus.FAILED
    assert result.error.code == "authentication_required"
    assert result.error.retryable
    assert failed.state == QueueState.FAILED
    assert failed.attempt_count == 1


@pytest.mark.parametrize(
    "label",
    ["Preview PNG", "Thumbnail", "Web preview", "Original with watermark"],
)
def test_preview_and_thumbnail_are_never_quality_candidates(label) -> None:
    with pytest.raises(AssetwayDownloadError, match="maior qualidade"):
        choose_best_quality([PageControl(0, "button", "option", label, "", "", "", False)])


def test_original_and_vector_source_quality_are_prioritized() -> None:
    controls = [
        PageControl(0, "button", "option", "High resolution JPG", "", "", "", False),
        PageControl(1, "button", "option", "Original PNG", "", "", "", False),
        PageControl(2, "button", "option", "Original file EPS", "", "", "", False),
    ]

    selection = choose_best_quality(controls)

    assert selection.control.index == 2
    assert selection.source_format == "EPS"
    assert selection.rank > choose_best_quality(controls[:2]).rank


def test_page_control_click_expression_normalizes_visible_whitespace() -> None:
    class Cdp:
        expression = ""

        def evaluate_page(self, target_id: str, expression: str):
            self.expression = expression
            return True

    cdp = Cdp()
    model = AssetwayPageModel(cdp, "target-1")
    model.click(PageControl(2, "button", "option", "Original PNG", "", "", "", False))

    assert 'replace(/\\s+/g, " ")' in cdp.expression


def test_page_model_finds_and_clicks_control_in_accessible_child_frame() -> None:
    class FrameCdp:
        clicked_frame = None

        def evaluate_page(self, target_id: str, expression: str):
            return make_snapshot(controls=[])

        def frame_ids(self, target_id: str):
            return ("main-frame", "download-frame")

        def evaluate_frame(self, target_id: str, frame_id: str, expression: str):
            if expression == PAGE_INSPECTION_EXPRESSION:
                return make_snapshot(
                    controls=[
                        {
                            "index": 0,
                            "tag": "button",
                            "role": "button",
                            "text": "Baixar original SVG",
                            "ariaLabel": "Download original",
                            "title": "",
                            "popup": "",
                            "disabled": False,
                        }
                    ]
                )
            self.clicked_frame = frame_id
            return True

    cdp = FrameCdp()
    model = AssetwayPageModel(cdp, "target-1")

    snapshot = model.inspect()
    control = snapshot.find_download_action()
    model.click(control)

    assert control.frame_id == "download-frame"
    assert cdp.clicked_frame == "download-frame"


def test_real_modal_structure_beats_header_and_proves_direct_original() -> None:
    header = PageControl(
        1, "button", "", "Baixar", "", "", "", False, context="page"
    )
    action = PageControl(
        10,
        "button",
        "",
        "Baixar",
        "",
        "",
        "",
        False,
        context="dialog",
        group_index=0,
        sibling_index=0,
    )
    trigger = PageControl(
        11,
        "button",
        "",
        "",
        "",
        "",
        "",
        False,
        context="dialog",
        group_index=0,
        sibling_index=1,
        icon_only=True,
    )
    format_control = PageControl(
        12, "button", "", "EPS", "", "", "", False, context="dialog"
    )
    filename = PageControl(
        13,
        "button",
        "",
        "asset-original.eps",
        "",
        "",
        "",
        False,
        context="dialog",
    )
    snapshot = AssetwayPageSnapshot(
        "complete",
        "plataformaa.assetway.com.br",
        "/p/acervo/search",
        False,
        (header, action, trigger, format_control, filename),
        body_ready=True,
    )

    selected_action = snapshot.find_download_action()
    selected_trigger = snapshot.find_download_menu_trigger(selected_action)
    quality = AssetwayDownloader._direct_original_selection(selected_action, snapshot)

    assert selected_action is action
    assert selected_trigger is trigger
    assert quality.control is action
    assert quality.quality_label == "Original EPS"
    assert quality.source_format == "EPS"


def test_absent_quality_evidence_marks_failed_without_clicking_download(tmp_path) -> None:
    snapshot = make_snapshot(controls=[make_snapshot()["controls"][0]])
    downloader, queue, item, cdp, _ = build_downloader(tmp_path, snapshot=snapshot)

    result = downloader.download_item(queue, item.item_id)

    assert result.status == DownloadStatus.FAILED
    assert result.error.code == "quality_unverified"
    assert not cdp.clicked_expressions
    assert queue.get_item(item.item_id).state == QueueState.FAILED


@pytest.mark.parametrize(
    ("monitor_options", "expected_error"),
    [
        ({"write_file": False}, "download_incomplete"),
        ({"temporary": True}, "download_incomplete"),
        ({"content": b""}, "download_incomplete"),
        ({"reported_total_bytes": len(PNG_BYTES) + 10}, "download_incomplete"),
        ({"content": b"<!doctype html><html>error</html>"}, "download_invalid_content"),
    ],
)
def test_incomplete_empty_temporary_and_html_files_fail(
    tmp_path, monitor_options, expected_error
) -> None:
    downloader, queue, item, _, runtime_root = build_downloader(
        tmp_path,
        monitor_options=monitor_options,
    )

    result = downloader.download_item(queue, item.item_id)

    assert result.status == DownloadStatus.FAILED
    assert result.error.code == expected_error
    assert queue.get_item(item.item_id).state == QueueState.FAILED
    assert not list((runtime_root / "downloads").rglob("*.png"))


def test_extension_must_match_the_selected_official_format(tmp_path) -> None:
    snapshot = make_snapshot()
    snapshot["controls"][1]["text"] = "Original file SVG"
    downloader, queue, item, _, _ = build_downloader(tmp_path, snapshot=snapshot)

    result = downloader.download_item(queue, item.item_id)

    assert result.status == DownloadStatus.FAILED
    assert result.error.code == "download_format_mismatch"
    assert queue.get_item(item.item_id).state == QueueState.FAILED


def test_official_filename_is_sanitized_only_for_windows_invalid_characters(tmp_path) -> None:
    downloader, queue, item, _, _ = build_downloader(
        tmp_path,
        monitor_options={"filename": r"C:\asset\Official: image.png"},
    )

    result = downloader.download_item(queue, item.item_id)

    assert result.status == DownloadStatus.COMPLETED
    assert result.file_name == "Official_ image.png"
    assert result.extension == ".png"


def test_invalid_item_state_is_rejected_before_attempt_increment(tmp_path) -> None:
    downloader, queue, item, _, _ = build_downloader(tmp_path)
    queue.start_processing(item.item_id)

    with pytest.raises(AssetwayDownloadError, match="precisa estar pronto"):
        downloader.download_item(queue, item.item_id)

    assert queue.get_item(item.item_id).attempt_count == 1
    assert queue.get_item(item.item_id).state == QueueState.PROCESSING


def test_missing_download_action_produces_specific_failure(tmp_path) -> None:
    downloader, queue, item, _, _ = build_downloader(
        tmp_path,
        snapshot=make_snapshot(controls=[]),
    )

    result = downloader.download_item(queue, item.item_id)

    assert result.status == DownloadStatus.FAILED
    assert result.error.code == "download_action_unverified"
    assert queue.get_item(item.item_id).state == QueueState.FAILED


def test_dom_diagnostic_logs_only_sanitized_metadata_and_never_clicks(
    tmp_path, caplog
) -> None:
    snapshot = make_snapshot(
        controls=[
            {
                "index": 0,
                "tag": "div",
                "role": "button",
                "text": "Baixar original PNG",
                "ariaLabel": "Download",
                "title": "Original",
                "popup": "menu",
                "elementId": "download-control",
                "className": "token-private download-action",
                "testId": "asset-download",
                "disabled": False,
            }
        ]
    )
    downloader, queue, item, cdp, _ = build_downloader(tmp_path, snapshot=snapshot)
    caplog.set_level("INFO")

    candidates = downloader.diagnose_item(queue, item.item_id)

    assert candidates == 1
    assert queue.get_item(item.item_id).state == QueueState.READY
    assert not cdp.clicked_expressions
    assert "tag=div" in caplog.text
    assert "class_name=[redacted]" in caplog.text
    assert "token-private" not in caplog.text
    assert "<html" not in caplog.text.casefold()
    assert "cookie=" not in caplog.text.casefold()


def test_dom_inspection_includes_non_button_semantic_candidates() -> None:
    assert "[onclick]" in PAGE_INSPECTION_EXPRESSION
    assert "element.shadowRoot" in PAGE_INSPECTION_EXPRESSION
    assert "data-action" in PAGE_INSPECTION_EXPRESSION
    assert ",div,span" in PAGE_INSPECTION_EXPRESSION


def test_assetway_automatic_download_starts_background_chrome(tmp_path) -> None:
    downloader, queue, item, _, _ = build_downloader(tmp_path)
    opened_modes: list[ChromeMode] = []
    downloader.runtime.process_manager.is_running = lambda provider: False
    downloader.runtime.open_provider = lambda provider, *, mode: (
        opened_modes.append(mode) or SimpleNamespace(target_id="asset-page")
    )

    result = downloader.download_item(queue, item.item_id)

    assert result.status == DownloadStatus.COMPLETED
    assert opened_modes == [ChromeMode.BACKGROUND_HEADED]


def test_assetway_chrome_start_recovers_once_before_download(tmp_path) -> None:
    downloader, queue, item, _, _ = build_downloader(tmp_path)
    attempts = 0
    close_calls = 0
    recovery_calls = 0
    downloader.runtime.process_manager.is_running = lambda provider: False

    def open_provider(provider, *, mode):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ChromeProfileInUseError("stale startup state")
        return SimpleNamespace(target_id="asset-page")

    def close_provider(provider):
        nonlocal close_calls
        close_calls += 1
        return 0.0

    def recover_provider(provider):
        nonlocal recovery_calls
        recovery_calls += 1

    downloader.runtime.open_provider = open_provider
    downloader.runtime.close_provider = close_provider
    downloader.runtime.recover_provider = recover_provider

    result = downloader.download_item(queue, item.item_id)

    assert result.status == DownloadStatus.COMPLETED
    assert attempts == 2
    assert close_calls == 1
    assert recovery_calls == 1


def test_assetway_chrome_start_failure_has_granular_code_and_safe_log(
    tmp_path,
    caplog,
) -> None:
    downloader, queue, item, _, _ = build_downloader(tmp_path)
    downloader.runtime.process_manager.is_running = lambda provider: False
    downloader.runtime.open_provider = lambda provider, *, mode: (_ for _ in ()).throw(
        ChromeStartupError("token=private https://secret.example/path")
    )
    downloader.runtime.close_provider = lambda provider: 0.0
    caplog.set_level("WARNING")

    result = downloader.download_item(queue, item.item_id)

    assert result.status == DownloadStatus.FAILED
    assert result.error is not None
    assert result.error.code == "chrome_start_failed"
    assert "stage=chrome_start" in caplog.text
    assert "exception=ChromeStartupError" in caplog.text
    assert "private" not in caplog.text
    assert "secret.example" not in caplog.text
