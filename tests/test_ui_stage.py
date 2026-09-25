from __future__ import annotations

import os
import threading
import zipfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEventLoop, QMimeData, QPointF, Qt, QTimer, QUrl
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import QApplication

from image_downloader.input.models import SourceType, UrlOccurrence, UrlRecord
from image_downloader.providers.models import ProviderId
from image_downloader.providers.registry import ProviderRegistry
from image_downloader.ui.controllers.input_controller import InputController
from image_downloader.ui.main_window import MainWindow
from image_downloader.ui.widgets.drop_zone import DropZone
from image_downloader.ui.workers.input_worker import AnalysisRequest, InputSource


@pytest.fixture(scope="session")
def qt_app():
    return QApplication.instance() or QApplication([])


def write_pptx(path: Path, text: str) -> Path:
    slide_xml = (
        '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        "<p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>"
        f"{text}"
        "</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>"
    )
    with zipfile.ZipFile(path, "w") as package:
        package.writestr("ppt/slides/slide1.xml", slide_xml)
    return path


def wait_for_analysis(window: MainWindow, action, timeout_ms: int = 8000):
    loop = QEventLoop()
    timer = QTimer()
    timer.setSingleShot(True)
    results = []

    def on_finished(result) -> None:
        results.append(result)
        loop.quit()

    window.controller.analysis_finished.connect(on_finished)
    timer.timeout.connect(loop.quit)
    timer.start(timeout_ms)
    action()
    loop.exec()
    window.controller.analysis_finished.disconnect(on_finished)
    assert results, "Analysis did not finish before the timeout."
    return results[-1]


def test_main_window_starts_with_functional_controls(qt_app) -> None:
    window = MainWindow()
    assert window.windowTitle() == "Image Downloader"
    assert window.drop_zone.acceptDrops()
    assert window.links_edit.placeholderText() == "Cole um ou vários links aqui..."
    assert window.queue_model.rowCount() == 0
    window.close()


def test_drop_zone_accepts_multiple_pptx_and_rejects_other_extensions(qt_app) -> None:
    zone = DropZone()
    accepted, rejected = zone.partition_paths(
        ["lesson-one.pptx", "lesson-two.PPTX", "notes.pdf"]
    )
    assert accepted == ["lesson-one.pptx", "lesson-two.PPTX"]
    assert rejected == ["notes.pdf"]

    emitted_paths: list[list[str]] = []
    zone.files_requested.connect(emitted_paths.append)
    mime_data = QMimeData()
    mime_data.setUrls(
        [
            QUrl.fromLocalFile("C:/courses/one.pptx"),
            QUrl.fromLocalFile("C:/courses/two.pptx"),
            QUrl.fromLocalFile("C:/courses/notes.pdf"),
        ]
    )
    event = QDropEvent(
        QPointF(10, 10),
        Qt.DropAction.CopyAction,
        mime_data,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    zone.dropEvent(event)
    assert event.isAccepted()
    assert len(emitted_paths[0]) == 2
    zone.close()


def test_text_input_runs_provider_queue_flow_and_updates_summary(qt_app) -> None:
    window = MainWindow()
    main_thread_id = threading.get_ident()
    ui_signal_thread_ids: list[int] = []
    window.controller.analysis_finished.connect(
        lambda result: ui_signal_thread_ids.append(threading.get_ident())
    )
    text = "\n".join(
        [
            "Assetway https://plataformaa.assetway.com.br/p/acervo/search?assetId=65507",
            "Shutterstock https://www.shutterstock.com/image-photo/forest-123456789",
            "Envato https://elements.envato.com/example-item-ABC1234",
            "Other https://example.com/image/unknown",
            "Repeated other https://example.com/image/unknown",
        ]
    )
    result = wait_for_analysis(window, lambda: window.links_edit.setPlainText(text))

    assert result.accepted
    assert result.worker_thread_id != main_thread_id
    assert ui_signal_thread_ids == [main_thread_id]
    summary = result.snapshot.summary
    assert summary.total == 4
    assert summary.provider_counts[ProviderId.ASSETWAY] == 1
    assert summary.provider_counts[ProviderId.SHUTTERSTOCK] == 1
    assert summary.provider_counts[ProviderId.ENVATO] == 1
    assert summary.blocked == 1
    assert window.queue_model.rowCount() == 4
    providers = [
        window.queue_model.data(window.queue_model.index(row, 1)) for row in range(4)
    ]
    assert set(providers) == {"Assetway", "Shutterstock", "Envato", "Não suportado"}
    states = [window.queue_model.data(window.queue_model.index(row, 0)) for row in range(4)]
    assert states.count("Não suportado") == 1
    unsupported_row = providers.index("Não suportado")
    assert window.queue_model.data(window.queue_model.index(unsupported_row, 3)) == "2 origens"
    displayed = [
        window.queue_model.data(window.queue_model.index(row, column))
        for row in range(4)
        for column in range(4)
    ]
    assert all("assetId" not in str(value) for value in displayed)
    assert result.analysis_ms >= 0
    assert result.ui_elapsed_ms >= result.analysis_ms
    previous_batch_id = window.controller.batch_id
    window.clear_button.click()
    assert window.queue_model.rowCount() == 0
    assert window.summary_values["total"].text() == "0"
    assert window.controller.queue_manager.summary().total == 0
    assert window.controller.batch_id != previous_batch_id
    window.close()


def test_blank_and_non_url_text_do_not_create_items(qt_app) -> None:
    window = MainWindow()
    result = wait_for_analysis(
        window,
        lambda: window.links_edit.setPlainText("there are no links here"),
    )
    assert result.snapshot.summary.total == 0
    assert window.queue_model.rowCount() == 0
    window.links_edit.setPlainText("   ")
    assert window.controller.queue_manager.summary().total == 0
    window.controller._debounce_timer.stop()
    window.close()


def test_invalid_pptx_warning_does_not_block_valid_file(qt_app, tmp_path) -> None:
    window = MainWindow()
    invalid_path = tmp_path / "broken.pptx"
    invalid_path.write_bytes(b"not a pptx")
    valid_path = write_pptx(
        tmp_path / "valid.pptx",
        "https://www.shutterstock.com/image-photo/forest-123456789",
    )
    existing_path = write_pptx(
        tmp_path / "existing.pptx",
        "https://elements.envato.com/example-item-ABC1234",
    )
    wait_for_analysis(window, lambda: window.controller.add_files([str(existing_path)]))
    result = wait_for_analysis(
        window,
        lambda: window.controller.add_files([str(invalid_path), str(valid_path)]),
    )
    assert result.snapshot.summary.total == 2
    assert {item.provider for item in result.snapshot.items} == {
        ProviderId.ENVATO,
        ProviderId.SHUTTERSTOCK,
    }
    assert invalid_path.name in window.warning_label.text()
    assert not window.warning_label.isHidden()
    window.close()


def test_same_canonical_file_is_only_submitted_once(qt_app, tmp_path) -> None:
    window = MainWindow()
    pptx_path = write_pptx(
        tmp_path / "same.pptx",
        "https://elements.envato.com/example-item-ABC1234",
    )
    result = wait_for_analysis(
        window,
        lambda: (
            window.controller.add_files([str(pptx_path)]),
            window.controller.add_files([str(pptx_path.resolve())]),
        ),
    )
    assert result.snapshot.summary.total == 1
    assert "já foi adicionado" in window.warning_label.text()
    window.close()


def test_text_and_pptx_origins_merge_into_one_queue_row(qt_app, tmp_path) -> None:
    window = MainWindow()
    url = "https://elements.envato.com/example-item-ABC1234"
    pptx_path = write_pptx(tmp_path / "source.pptx", url)
    wait_for_analysis(window, lambda: window.controller.add_files([str(pptx_path)]))
    result = wait_for_analysis(window, lambda: window.links_edit.setPlainText(url))
    assert result.snapshot.summary.total == 1
    assert len(result.snapshot.items[0].occurrences) == 2
    assert window.queue_model.rowCount() == 1
    assert window.queue_model.data(window.queue_model.index(0, 3)) == "2 origens"
    window.close()


def test_stale_text_generation_and_clear_reject_old_commit(qt_app) -> None:
    controller = InputController()
    controller.schedule_text_analysis("https://elements.envato.com/example-item-ABC1234")
    old_text_generation = controller.text_generation
    old_batch_generation = controller.batch_generation
    controller.schedule_text_analysis("https://www.shutterstock.com/search/forest")
    old_record = UrlRecord.from_occurrences(
        [
            UrlOccurrence(
                original_url="https://elements.envato.com/example-item-ABC1234",
                normalized_url="https://elements.envato.com/example-item-ABC1234",
                source_type=SourceType.pasted_text,
            )
        ]
    )
    old_request = AnalysisRequest(
        request_id="stale-request",
        batch_generation=old_batch_generation,
        text_generation=old_text_generation,
        source=InputSource.TEXT,
        text="stale",
        paths=(),
        submitted_at_ns=0,
    )
    old_classification = ProviderRegistry().classify_records([old_record])
    assert controller._commit_request(old_request, old_classification) is None
    assert controller.queue_manager.summary().total == 0

    controller.clear_batch()
    stale_after_clear = AnalysisRequest(
        request_id="pre-clear-request",
        batch_generation=old_batch_generation,
        text_generation=None,
        source=InputSource.FILES,
        text="",
        paths=(),
        submitted_at_ns=0,
    )
    assert controller._commit_request(stale_after_clear, old_classification) is None
    assert controller.queue_manager.summary().total == 0


def test_clear_removes_visual_and_domain_state_and_starts_new_batch(qt_app) -> None:
    window = MainWindow()
    wait_for_analysis(
        window,
        lambda: window.links_edit.setPlainText(
            "https://plataformaa.assetway.com.br/p/acervo/search?assetId=7"
        ),
    )
    previous_batch_id = window.controller.batch_id
    window.clear_button.click()
    assert window.links_edit.toPlainText() == ""
    assert window.queue_model.rowCount() == 0
    assert window.summary_values["total"].text() == "0"
    assert window.warning_label.text() == ""
    assert window.controller.queue_manager.summary().total == 0
    assert window.controller.batch_id != previous_batch_id
    window.close()


def test_queue_model_labels_unknown_as_unsupported(qt_app) -> None:
    window = MainWindow()
    result = wait_for_analysis(
        window,
        lambda: window.links_edit.setPlainText("https://example.com/unknown"),
    )
    assert result.snapshot.summary.blocked == 1
    assert window.queue_model.data(window.queue_model.index(0, 0)) == "Não suportado"
    assert window.queue_model.data(window.queue_model.index(0, 1)) == "Não suportado"
    window.close()


def test_responsive_splitter_changes_orientation(qt_app) -> None:
    window = MainWindow()
    window.show()
    window.resize(900, 760)
    qt_app.processEvents()
    assert window._splitter.orientation() == Qt.Orientation.Vertical
    window.resize(1120, 760)
    qt_app.processEvents()
    assert window._splitter.orientation() == Qt.Orientation.Horizontal
    window.close()