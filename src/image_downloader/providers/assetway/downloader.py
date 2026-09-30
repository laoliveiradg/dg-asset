"""Single-item Assetway download through the app-managed Chrome session."""

from __future__ import annotations

import logging
import re
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from urllib.parse import parse_qs, urlsplit

from image_downloader.chrome.cdp_client import CdpClient
from image_downloader.chrome.models import (
    ChromeMode,
    ChromeProfileInUseError,
    ChromeRuntimeError,
)
from image_downloader.chrome.runtime import ChromeRuntime
from image_downloader.downloads.models import (
    DownloadFailure,
    DownloadResult,
    DownloadStatus,
    DownloadTimings,
)
from image_downloader.providers.assetway.errors import (
    AssetwayDownloadError,
    authentication_required,
    quality_unverified,
)
from image_downloader.providers.assetway.page_model import (
    AssetwayPageModel,
    AssetwayPageSnapshot,
    PageControl,
)
from image_downloader.providers.assetway.quality import QualitySelection, choose_best_quality
from image_downloader.providers.models import ProviderId
from image_downloader.queue.manager import QueueManager
from image_downloader.queue.models import QueueError, QueueItem, QueueState

logger = logging.getLogger(__name__)

ASSETWAY_HOSTS = {"plataformaa.assetway.com.br", "i.assetway.com.br"}
DOWNLOAD_TIMEOUT_SECONDS = 180.0
PAGE_TIMEOUT_SECONDS = 25.0
POLL_INTERVAL_SECONDS = 0.15

_FORMAT_SIGNATURES = {
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
    ".png": "PNG",
    ".gif": "GIF",
    ".webp": "WEBP",
    ".tif": "TIFF",
    ".tiff": "TIFF",
    ".bmp": "BMP",
    ".svg": "SVG",
    ".eps": "EPS",
    ".ai": "AI",
}
_INVALID_WINDOWS_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@dataclass(frozen=True, slots=True)
class AssetwayPageSession:
    target_id: str
    cdp: CdpClient
    queue_item: QueueItem
    page: AssetwayPageModel


class AssetwayDownloader:
    """Process one READY Assetway item; all state changes go through QueueManager."""

    def __init__(
        self,
        runtime: ChromeRuntime,
        *,
        page_timeout: float = PAGE_TIMEOUT_SECONDS,
        download_timeout: float = DOWNLOAD_TIMEOUT_SECONDS,
        poll_interval: float = POLL_INTERVAL_SECONDS,
    ) -> None:
        self.runtime = runtime
        self.page_timeout = page_timeout
        self.download_timeout = download_timeout
        self.poll_interval = poll_interval

    @property
    def download_root(self) -> Path:
        runtime_root = self.runtime.process_manager.profile_factory.runtime_root
        return (runtime_root / "downloads").resolve()

    def download_item(
        self,
        queue_manager: QueueManager,
        item_id: str,
        progress: Callable[[str], None] | None = None,
    ) -> DownloadResult:
        item = queue_manager.get_item(item_id)
        if item.provider != ProviderId.ASSETWAY:
            raise AssetwayDownloadError(
                "provider_unsupported",
                "Este downloader aceita somente itens Assetway.",
                retryable=False,
            )
        if item.state != QueueState.READY:
            raise AssetwayDownloadError(
                "item_not_ready",
                "O item precisa estar pronto antes de iniciar o download.",
                retryable=False,
            )
        self._validate_item_url(item)

        processing_item = queue_manager.start_processing(item_id)
        started = perf_counter()
        timings = {
            "navigation_ms": 0.0,
            "action_lookup_ms": 0.0,
            "quality_discovery_ms": 0.0,
            "download_start_ms": 0.0,
            "transfer_ms": 0.0,
            "validation_ms": 0.0,
        }
        attempt_directory: Path | None = None
        stage = "prepare"
        self._emit(progress, "Preparando ativo...")
        logger.info("provider=ASSETWAY item_id=%s state=PROCESSING", item.item_id)

        try:
            navigation_started = perf_counter()
            self._emit(progress, "Abrindo ativo...")
            try:
                stage = "chrome_start"
                target_id = self._managed_target_id()
                stage = "cdp_connection"
                cdp = self.runtime.cdp_client_for(ProviderId.ASSETWAY)
                cdp.check_connection()
                pages = cdp.list_pages()
                if not any(target.target_id == target_id for target in pages):
                    target_id = cdp.open_page("about:blank")
                    pages = cdp.list_pages()
                logger.info(
                    "provider=ASSETWAY target_count=%s selected_target_type=page",
                    len(pages),
                )
                page = AssetwayPageModel(cdp, target_id)
                session = AssetwayPageSession(target_id, cdp, item, page)
                stage = "navigation"
                cdp.navigate(target_id, item.normalized_url)
                stage = "page_inspection"
                snapshot = self._wait_for_asset_page(session)
            finally:
                timings["navigation_ms"] = (perf_counter() - navigation_started) * 1000

            self._emit(progress, "Localizando download...")
            stage = "download_action"
            action_started = perf_counter()
            try:
                snapshot, action = self._wait_for_download_action(session, snapshot)
            finally:
                timings["action_lookup_ms"] = (perf_counter() - action_started) * 1000

            self._emit(progress, "Selecionando qualidade...")
            stage = "quality"
            quality_started = perf_counter()
            try:
                menu_trigger = snapshot.find_download_menu_trigger(action)
                if menu_trigger is not None:
                    previous_controls = {
                        self._control_signature(control) for control in snapshot.controls
                    }
                    page.click(menu_trigger)
                    try:
                        snapshot, selection = self._wait_for_quality_options(
                            session,
                            previous_controls,
                        )
                    except AssetwayDownloadError:
                        snapshot = session.page.inspect()
                        selection = self._direct_original_selection(action, snapshot)
                        page.click(menu_trigger)
                else:
                    try:
                        selection = choose_best_quality(snapshot.controls)
                    except AssetwayDownloadError:
                        if not action.opens_popup:
                            raise quality_unverified()
                        previous_controls = {
                            self._control_signature(control) for control in snapshot.controls
                        }
                        page.click(action)
                        snapshot, selection = self._wait_for_quality_options(
                            session,
                            previous_controls,
                        )
                if selection.rank < 250:
                    raise quality_unverified()
            finally:
                timings["quality_discovery_ms"] = (perf_counter() - quality_started) * 1000
            logger.info(
                "provider=ASSETWAY item_id=%s quality=%s",
                item.item_id,
                selection.quality_label[:80],
            )

            attempt_directory = self._prepare_attempt_directory(processing_item)
            stage = "download_start"
            expected_frame_id = selection.control.frame_id or cdp.main_frame_id(target_id)
            cdp_download = cdp.monitor_downloads(attempt_directory)
            with cdp_download as monitor:
                click_started = perf_counter()
                try:
                    self._require_current_asset_page(session)
                    page.click(selection.control)
                    direct_action = (
                        action.frame_id == selection.control.frame_id
                        and action.index == selection.control.index
                    )
                    download_started = monitor.wait_for_start(
                        timeout=(min(self.download_timeout, 15.0) if direct_action else 1.0),
                        expected_frame_id=expected_frame_id,
                    )
                    if not download_started and not direct_action:
                        self._require_current_asset_page(session)
                        page.click(action)
                        download_started = monitor.wait_for_start(
                            timeout=self.download_timeout,
                            expected_frame_id=expected_frame_id,
                        )
                    if not download_started:
                        raise AssetwayDownloadError(
                            "download_not_started",
                            "O Chrome não confirmou o início do download oficial.",
                            retryable=True,
                        )
                finally:
                    timings["download_start_ms"] = (perf_counter() - click_started) * 1000
                self._emit(progress, "Baixando...")
                stage = "transfer"
                transfer_started = perf_counter()
                try:
                    event = monitor.wait_for_completion(timeout=self.download_timeout)
                finally:
                    timings["transfer_ms"] = (perf_counter() - transfer_started) * 1000

            self._emit(progress, "Validando...")
            stage = "validation"
            validation_started = perf_counter()
            try:
                file_path = self._wait_for_final_file(
                    attempt_directory,
                    event.suggested_filename,
                )
                bytes_received, detected_format = self._validate_file(file_path, event, selection)
            finally:
                timings["validation_ms"] = (perf_counter() - validation_started) * 1000

            completed_item = queue_manager.mark_completed(item_id)
            timings["total_ms"] = (perf_counter() - started) * 1000
            self._emit(progress, "Concluído")
            logger.info(
                "provider=ASSETWAY item_id=%s download_completed bytes=%s",
                completed_item.item_id,
                bytes_received,
            )
            return DownloadResult(
                item_id=item.item_id,
                provider=ProviderId.ASSETWAY,
                status=DownloadStatus.COMPLETED,
                file_path=file_path,
                file_name=file_path.name,
                extension=file_path.suffix.lower(),
                bytes_received=bytes_received,
                quality_label=selection.quality_label,
                source_format=detected_format,
                timings=DownloadTimings(**timings),
            )
        except AssetwayDownloadError as error:
            return self._mark_failed(
                queue_manager,
                item,
                error,
                timings,
                started,
                attempt_directory,
            )
        except Exception as unexpected:
            error = self._unexpected_error(stage, unexpected)
            return self._mark_failed(
                queue_manager,
                item,
                error,
                timings,
                started,
                attempt_directory,
            )

    def diagnose_item(self, queue_manager: QueueManager, item_id: str) -> int:
        """Inspect one Assetway item without clicking controls or changing queue state."""

        item = queue_manager.get_item(item_id)
        if item.provider != ProviderId.ASSETWAY:
            raise AssetwayDownloadError(
                "provider_unsupported",
                "O diagnóstico aceita somente itens Assetway.",
                retryable=False,
            )
        if item.state != QueueState.READY:
            raise AssetwayDownloadError(
                "item_not_ready",
                "O item precisa estar pronto para executar o diagnóstico.",
                retryable=False,
            )
        self._validate_item_url(item)
        target_id = self._managed_target_id()
        cdp = self.runtime.cdp_client_for(ProviderId.ASSETWAY)
        cdp.navigate(target_id, item.normalized_url)
        page = AssetwayPageModel(cdp, target_id)
        session = AssetwayPageSession(target_id, cdp, item, page)
        snapshot = self._wait_for_asset_page(session)
        snapshot.require_authenticated()
        return page.log_download_diagnostic(snapshot)

    def _managed_target_id(self) -> str:
        process_manager = self.runtime.process_manager
        managed = (
            process_manager.managed_record(ProviderId.ASSETWAY)
            if process_manager.is_running(ProviderId.ASSETWAY)
            else None
        )
        if managed is None or getattr(managed, "mode", None) != ChromeMode.BACKGROUND_HEADED:
            for attempt in range(2):
                try:
                    return self.runtime.open_provider(
                        ProviderId.ASSETWAY,
                        mode=ChromeMode.BACKGROUND_HEADED,
                    ).target_id
                except ChromeRuntimeError as error:
                    logger.warning(
                        "provider=ASSETWAY stage=chrome_start attempt=%s exception=%s message=%s",
                        attempt + 1,
                        type(error).__name__,
                        self._safe_exception_message(error),
                    )
                    self.runtime.close_provider(ProviderId.ASSETWAY)
                    if isinstance(error, ChromeProfileInUseError) and attempt == 0:
                        self.runtime.recover_provider(ProviderId.ASSETWAY)
                    if attempt == 1:
                        raise AssetwayDownloadError(
                            "chrome_start_failed",
                            "Não foi possível iniciar o Chrome gerenciado do Assetway.",
                            retryable=True,
                        ) from error
        return managed.target_id

    @classmethod
    def _unexpected_error(cls, stage: str, error: Exception) -> AssetwayDownloadError:
        code_by_stage = {
            "chrome_start": "chrome_start_failed",
            "cdp_connection": "cdp_connection_failed",
            "navigation": "navigation_failed",
            "page_inspection": "navigation_failed",
            "validation": "validation_failed",
        }
        code = code_by_stage.get(stage, "download_failed")
        logger.warning(
            "provider=ASSETWAY stage=%s exception=%s message=%s",
            stage,
            type(error).__name__,
            cls._safe_exception_message(error),
        )
        messages = {
            "chrome_start_failed": "Não foi possível iniciar o Chrome gerenciado do Assetway.",
            "cdp_connection_failed": "Não foi possível conectar ao Chrome gerenciado.",
            "navigation_failed": "Não foi possível abrir o ativo Assetway.",
            "validation_failed": "O arquivo baixado não passou pela validação.",
        }
        return AssetwayDownloadError(
            code,
            messages.get(
                code,
                "O download Assetway falhou. Verifique o acesso e tente novamente.",
            ),
            retryable=True,
        )

    @staticmethod
    def _safe_exception_message(error: Exception) -> str:
        message = " ".join(str(error).split())[:240]
        message = re.sub(r"https?://\S+", "[url]", message, flags=re.IGNORECASE)
        message = re.sub(
            r"(?i)(token|cookie|password|authorization|session)\s*[=:]\s*\S+",
            r"\1=[redacted]",
            message,
        )
        return message or "no_message"

    def _wait_for_asset_page(
        self,
        session: AssetwayPageSession,
    ) -> AssetwayPageSnapshot:
        deadline = time.monotonic() + self.page_timeout
        last_url = ""
        last_signature: tuple[object, ...] | None = None
        stable_polls = 0
        last_snapshot: AssetwayPageSnapshot | None = None
        while time.monotonic() < deadline:
            target = session.cdp.get_page(session.target_id)
            last_url = target.url
            snapshot = session.page.inspect()
            last_snapshot = snapshot
            if snapshot.login_required or self._is_login_url(last_url):
                raise authentication_required()
            if snapshot.ready_state in {"interactive", "complete"} and snapshot.body_ready:
                if not self._matches_asset(session.queue_item, last_url):
                    if urlsplit(last_url).hostname not in ASSETWAY_HOSTS:
                        raise AssetwayDownloadError(
                            "authentication_required",
                            "A sessão Assetway expirou. Entre manualmente e tente novamente.",
                            retryable=True,
                        )
                    time.sleep(self.poll_interval)
                    continue
                signature = (
                    snapshot.pathname,
                    snapshot.body_text_length,
                    snapshot.element_count,
                    len(snapshot.controls),
                )
                stable_polls = stable_polls + 1 if signature == last_signature else 0
                last_signature = signature
                if snapshot.controls or stable_polls >= 1:
                    return snapshot
            time.sleep(self.poll_interval)
        if self._is_login_url(last_url):
            raise authentication_required()
        if last_snapshot is not None:
            page_state = "empty" if not last_snapshot.body_ready else "unexpected_route"
            session.page.log_download_diagnostic(last_snapshot, page_state=page_state)
        raise AssetwayDownloadError(
            "asset_page_timeout",
            "A página do ativo Assetway não carregou no tempo esperado.",
            retryable=True,
        )

    def _wait_for_download_action(
        self,
        session: AssetwayPageSession,
        initial: AssetwayPageSnapshot,
    ) -> tuple[AssetwayPageSnapshot, PageControl]:
        deadline = time.monotonic() + min(self.page_timeout, 15.0)
        snapshot = initial
        while time.monotonic() < deadline:
            snapshot.require_authenticated()
            current_url = session.cdp.get_page(session.target_id).url
            if self._is_login_url(current_url):
                raise authentication_required()
            if not self._matches_asset(session.queue_item, current_url):
                time.sleep(self.poll_interval)
                snapshot = session.page.inspect()
                continue
            try:
                action = snapshot.find_download_action()
                expected_query = parse_qs(
                    urlsplit(session.queue_item.normalized_url).query,
                    keep_blank_values=True,
                )
                if (
                    expected_query.get("modal") == ["asset"]
                    and action.context != "dialog"
                ):
                    time.sleep(self.poll_interval)
                    snapshot = session.page.inspect()
                    continue
                return snapshot, action
            except AssetwayDownloadError:
                time.sleep(self.poll_interval)
                snapshot = session.page.inspect()
        page_state = "empty" if not snapshot.body_ready else "no_download_control"
        session.page.log_download_diagnostic(snapshot, page_state=page_state)
        snapshot.find_download_action()
        raise AssertionError("unreachable")

    def _wait_for_quality_options(
        self,
        session: AssetwayPageSession,
        previous_controls: set[tuple[object, ...]],
    ) -> tuple[AssetwayPageSnapshot, QualitySelection]:
        deadline = time.monotonic() + min(self.page_timeout, 8.0)
        unusable_new_polls = 0
        while time.monotonic() < deadline:
            snapshot = session.page.inspect()
            snapshot.require_authenticated()
            new_controls = [
                control
                for control in snapshot.controls
                if self._control_signature(control) not in previous_controls
            ]
            try:
                selection = choose_best_quality(new_controls)
            except AssetwayDownloadError:
                unusable_new_polls = unusable_new_polls + 1 if new_controls else 0
                if unusable_new_polls >= 2:
                    raise quality_unverified()
                time.sleep(self.poll_interval)
                continue
            return snapshot, selection
        raise quality_unverified()

    @staticmethod
    def _control_signature(control: PageControl) -> tuple[object, ...]:
        return (
            control.frame_id,
            control.tag,
            control.role,
            control.display_label.casefold(),
            control.context,
            control.class_name,
            control.group_index,
            control.sibling_index,
        )

    @staticmethod
    def _direct_original_selection(
        action: PageControl,
        snapshot: AssetwayPageSnapshot,
    ) -> QualitySelection:
        if action.context != "dialog":
            raise quality_unverified()
        extension_pattern = re.compile(r"\.(eps|ai|svg|tiff?|png|jpe?g|webp|gif|bmp)\b", re.I)
        exact_pattern = re.compile(r"^(EPS|AI|SVG|TIFF?|PNG|JPE?G|WEBP|GIF|BMP)$", re.I)
        filename_formats: set[str] = set()
        exact_formats: set[str] = set()
        for control in snapshot.controls:
            if control.context != "dialog":
                continue
            label = control.display_label.strip()
            if extension := extension_pattern.search(label):
                filename_formats.add(extension.group(1).upper())
            if exact := exact_pattern.fullmatch(label):
                exact_formats.add(exact.group(1).upper())
        normalized = {
            "JPG": "JPEG",
            "TIF": "TIFF",
        }
        filename_formats = {normalized.get(value, value) for value in filename_formats}
        exact_formats = {normalized.get(value, value) for value in exact_formats}
        proven_formats = filename_formats & exact_formats
        if len(proven_formats) != 1:
            raise quality_unverified()
        source_format = proven_formats.pop()
        rank = 750 if source_format in {"EPS", "AI", "SVG"} else 600
        return QualitySelection(
            action,
            f"Original {source_format}",
            source_format,
            rank,
        )

    def _require_current_asset_page(
        self,
        session: AssetwayPageSession,
    ) -> None:
        current_url = session.cdp.get_page(session.target_id).url
        if not self._matches_asset(session.queue_item, current_url):
            raise AssetwayDownloadError(
                "asset_page_changed",
                "A página mudou antes da ação oficial de download.",
                retryable=True,
            )
        session.page.inspect().require_authenticated()

    def _prepare_attempt_directory(self, item: QueueItem) -> Path:
        directory = (
            self.download_root
            / item.batch_id
            / item.item_id
            / f"attempt-{item.attempt_count:04d}"
        )
        directory.mkdir(parents=True, exist_ok=False)
        return directory

    def _wait_for_final_file(self, directory: Path, suggested_filename: str) -> Path:
        safe_name = self._sanitize_filename(suggested_filename)
        expected_path = directory / safe_name
        deadline = time.monotonic() + self.download_timeout
        previous_size = -1
        stable_polls = 0
        while time.monotonic() < deadline:
            if any(path.suffix.lower() == ".crdownload" for path in directory.iterdir()):
                stable_polls = 0
                time.sleep(self.poll_interval)
                continue
            final_files = [path for path in directory.iterdir() if path.is_file()]
            if len(final_files) > 1:
                raise AssetwayDownloadError(
                    "download_file_unverified",
                    "Mais de um arquivo apareceu no diretório do ativo.",
                    retryable=False,
                )
            if expected_path.is_file():
                current_size = expected_path.stat().st_size
            elif len(final_files) == 1:
                expected_path = final_files[0]
                current_size = expected_path.stat().st_size
            else:
                time.sleep(self.poll_interval)
                continue
            if current_size > 0 and current_size == previous_size:
                stable_polls += 1
                if stable_polls >= 2:
                    return expected_path
            else:
                stable_polls = 0
            previous_size = current_size
            time.sleep(self.poll_interval)
        raise AssetwayDownloadError(
            "download_incomplete",
            "O arquivo final do download não ficou disponível por completo.",
            retryable=True,
        )

    def _validate_file(
        self,
        file_path: Path,
        event,
        selection: QualitySelection,
    ) -> tuple[int, str]:
        if file_path.suffix.lower() == ".crdownload":
            raise AssetwayDownloadError(
                "download_incomplete",
                "O arquivo temporário do Chrome não é um resultado concluído.",
                retryable=True,
            )
        if not file_path.is_file():
            raise AssetwayDownloadError(
                "download_file_missing",
                "O arquivo final do download não existe.",
                retryable=True,
            )
        size = file_path.stat().st_size
        if size <= 0 or event.received_bytes != size:
            raise AssetwayDownloadError(
                "download_incomplete",
                "O tamanho recebido não corresponde ao arquivo final.",
                retryable=True,
            )
        if event.total_bytes > 0 and event.received_bytes != event.total_bytes:
            raise AssetwayDownloadError(
                "download_incomplete",
                "O Chrome não confirmou a transferência completa do arquivo.",
                retryable=True,
            )

        with file_path.open("rb") as downloaded_file:
            prefix = downloaded_file.read(4096)
        stripped = prefix.lstrip(b"\xef\xbb\xbf\x00\t\r\n ").lower()
        if stripped.startswith((b"<!doctype html", b"<html", b"<head", b"<body")):
            raise AssetwayDownloadError(
                "download_invalid_content",
                "O arquivo recebido é uma página HTML, não o ativo solicitado.",
                retryable=True,
            )
        detected_format = self._detect_format(file_path.suffix.lower(), prefix)
        if detected_format is None:
            raise AssetwayDownloadError(
                "download_format_unverified",
                "O formato do arquivo recebido não pôde ser comprovado.",
                retryable=False,
            )
        if selection.source_format and detected_format != selection.source_format:
            raise AssetwayDownloadError(
                "download_format_mismatch",
                "O formato recebido não corresponde à opção oficial selecionada.",
                retryable=False,
            )
        return size, detected_format

    @staticmethod
    def _detect_format(extension: str, prefix: bytes) -> str | None:
        expected = _FORMAT_SIGNATURES.get(extension)
        if expected is None:
            return None
        if expected == "JPEG" and prefix.startswith(b"\xff\xd8\xff"):
            return expected
        if expected == "PNG" and prefix.startswith(b"\x89PNG\r\n\x1a\n"):
            return expected
        if expected == "GIF" and prefix.startswith((b"GIF87a", b"GIF89a")):
            return expected
        if (
            expected == "WEBP"
            and len(prefix) >= 12
            and prefix[:4] == b"RIFF"
            and prefix[8:12] == b"WEBP"
        ):
            return expected
        if expected == "TIFF" and prefix.startswith((b"II*\x00", b"MM\x00*")):
            return expected
        if expected == "BMP" and prefix.startswith(b"BM"):
            return expected
        if expected == "SVG" and re.search(rb"<svg(?:\s|>)", prefix, re.IGNORECASE):
            return expected
        if expected == "EPS" and prefix.startswith(b"%!PS-Adobe"):
            return expected
        if expected == "AI" and prefix.startswith((b"%PDF-", b"%!PS-Adobe")):
            return expected
        return None

    @staticmethod
    def _sanitize_filename(suggested_filename: str) -> str:
        basename = suggested_filename.replace("\\", "/").rsplit("/", maxsplit=1)[-1]
        sanitized = _INVALID_WINDOWS_FILENAME.sub("_", basename).rstrip(" .")
        if not sanitized or sanitized in {".", ".."}:
            raise AssetwayDownloadError(
                "download_filename_unverified",
                "O nome oficial do arquivo não pôde ser preservado com segurança.",
                retryable=False,
            )
        return sanitized

    @staticmethod
    def _matches_asset(item: QueueItem, current_url: str) -> bool:
        original = urlsplit(item.normalized_url)
        current = urlsplit(current_url)
        if current.scheme not in {"http", "https"} or current.hostname not in ASSETWAY_HOSTS:
            return False
        current_query = parse_qs(current.query, keep_blank_values=True)
        expected_reference = item.asset_reference
        if expected_reference:
            actual_reference = (
                current_query.get("assetId") or current_query.get("assetid") or [None]
            )[0]
            return actual_reference == expected_reference
        return (
            current.hostname == original.hostname
            and current.path == original.path
            and current.query == original.query
        )

    @staticmethod
    def _validate_item_url(item: QueueItem) -> None:
        parsed = urlsplit(item.normalized_url)
        if parsed.scheme not in {"https", "http"} or parsed.hostname not in ASSETWAY_HOSTS:
            raise AssetwayDownloadError(
                "asset_url_invalid",
                "A URL do item não é um endereço Assetway válido.",
                retryable=False,
            )

    @staticmethod
    def _is_login_url(url: str) -> bool:
        path = urlsplit(url).path
        return bool(re.search(r"/(login|signin|sign-in|auth)(/|$)", path, re.IGNORECASE))

    @staticmethod
    def _emit(progress: Callable[[str], None] | None, message: str) -> None:
        if progress is not None:
            progress(message)

    @staticmethod
    def _mark_failed(
        queue_manager: QueueManager,
        item: QueueItem,
        error: AssetwayDownloadError,
        timings: dict[str, float],
        started: float,
        attempt_directory: Path | None,
    ) -> DownloadResult:
        if attempt_directory is not None:
            shutil.rmtree(attempt_directory, ignore_errors=True)
        failure = DownloadFailure(error.code, error.safe_message, error.retryable)
        queue_manager.mark_failed(
            item.item_id,
            QueueError(
                code=error.code,
                safe_message=error.safe_message,
                provider=ProviderId.ASSETWAY,
                retryable=error.retryable,
            ),
        )
        timings["total_ms"] = (perf_counter() - started) * 1000
        logger.info(
            "provider=ASSETWAY item_id=%s state=FAILED error=%s",
            item.item_id,
            error.code,
        )
        return DownloadResult(
            item_id=item.item_id,
            provider=ProviderId.ASSETWAY,
            status=DownloadStatus.FAILED,
            file_path=None,
            file_name=None,
            extension=None,
            bytes_received=0,
            quality_label=None,
            source_format=None,
            timings=DownloadTimings(**timings),
            error=failure,
        )
