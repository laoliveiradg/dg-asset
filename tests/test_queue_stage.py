from __future__ import annotations

from dataclasses import FrozenInstanceError
from zipfile import ZipFile

import pytest

from image_downloader.input.models import SourceType, UrlOccurrence, UrlRecord
from image_downloader.input.pptx_reader import extract_urls_from_pptx
from image_downloader.input.text_extractor import consolidate_urls, extract_urls_from_text
from image_downloader.providers import ProviderId, classify_url_records
from image_downloader.queue import (
    InvalidTransitionError,
    QueueError,
    QueueInvariantError,
    QueueManager,
    QueueState,
)
from image_downloader.queue.state_machine import validate_transition


def make_record(url: str, source: SourceType = SourceType.pasted_text) -> UrlRecord:
    occurrence = UrlOccurrence(
        original_url=url,
        normalized_url=url,
        source_type=source,
        source_name=source.value,
        context="fixture",
    )
    return UrlRecord.from_occurrences([occurrence])


def make_manager_with(url: str) -> tuple[QueueManager, str]:
    manager = QueueManager()
    item = manager.add_records([make_record(url)])[0]
    return manager, item.item_id


@pytest.mark.parametrize(
    ("url", "provider", "asset_reference"),
    [
        (
            "https://plataformaa.assetway.com.br/p/acervo/search?assetId=65507",
            ProviderId.ASSETWAY,
            "65507",
        ),
        (
            "https://www.shutterstock.com/image-photo/landscape-123456789",
            ProviderId.SHUTTERSTOCK,
            "123456789",
        ),
        (
            "https://elements.envato.com/example-item-ABC1234",
            ProviderId.ENVATO,
            "ABC1234",
        ),
    ],
)
def test_known_providers_create_ready_items_with_metadata(
    url: str, provider: ProviderId, asset_reference: str
) -> None:
    item = QueueManager().add_records([make_record(url)])[0]
    assert item.provider == provider
    assert item.asset_reference == asset_reference
    assert item.state == QueueState.READY
    assert item.normalized_url == url


def test_unknown_provider_is_blocked_without_rejecting_batch() -> None:
    manager = QueueManager()
    items = manager.add_records(
        [make_record("https://example.com/unknown"), make_record("https://elements.envato.com/item-ABCD123")]
    )
    assert [item.state for item in items] == [QueueState.BLOCKED, QueueState.READY]
    assert items[0].blocked_reason == "unsupported_provider"
    assert manager.summary().total == 2


def test_occurrences_and_provider_classification_are_preserved() -> None:
    record = make_record("https://plataformaa.assetway.com.br/p/acervo/search?assetId=77")
    classified = classify_url_records([record])
    item = QueueManager().add_classifications(classified)[0]
    assert item.occurrences == record.occurrences
    assert item.provider == ProviderId.ASSETWAY
    assert item.asset_reference == "77"
    assert item.provider_details["known_asset_path"] is True


def test_item_id_is_stable_and_does_not_expose_url() -> None:
    url = "https://elements.envato.com/item-ABC1234?token=private"
    first = QueueManager().add_records([make_record(url)])[0]
    second = QueueManager().add_records([make_record(url)])[0]
    assert first.item_id == second.item_id
    assert url not in first.item_id
    with pytest.raises(FrozenInstanceError):
        first.state = QueueState.COMPLETED


def test_distinct_urls_create_items_in_first_inclusion_order() -> None:
    manager = QueueManager()
    first_url = "https://elements.envato.com/item-ABC1234"
    second_url = "https://www.shutterstock.com/search/forest"
    first, second = manager.add_records([make_record(first_url), make_record(second_url)])
    assert len(manager.list_items()) == 2
    assert [item.normalized_url for item in manager.list_items()] == [first_url, second_url]
    assert manager.get_item(first.item_id) == first
    assert manager.get_by_url(second_url) == second


def test_duplicate_incremental_add_merges_origins_without_moving_item() -> None:
    manager = QueueManager()
    first_url = "https://elements.envato.com/item-ABC1234"
    second_url = "https://www.shutterstock.com/search/forest"
    manager.add_records([make_record(first_url, SourceType.slide_text), make_record(second_url)])
    merged = manager.add_records([make_record(first_url, SourceType.pasted_text)])
    assert len(manager.list_items()) == 2
    assert len(merged) == 1
    assert [item.normalized_url for item in manager.list_items()] == [first_url, second_url]
    assert [origin.source_type for origin in merged[0].occurrences] == [
        SourceType.slide_text,
        SourceType.pasted_text,
    ]


def test_duplicate_identical_occurrence_is_not_duplicated() -> None:
    record = make_record("https://elements.envato.com/item-ABC1234")
    item = QueueManager().add_records([record, record])[0]
    assert len(item.occurrences) == 1


def test_real_pptx_and_pasted_text_records_share_queue_item_model(tmp_path) -> None:
    url = "https://www.shutterstock.com/search/forest"
    pasted = consolidate_urls(extract_urls_from_text(url))[0]
    pptx_path = tmp_path / "sources.pptx"
    slide_xml = (
        '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        "<p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>"
        f"{url}"
        "</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>"
    )
    with ZipFile(pptx_path, "w") as package:
        package.writestr("ppt/slides/slide1.xml", slide_xml)
    pptx_origin = extract_urls_from_pptx(pptx_path)[0]
    manager = QueueManager()
    item = manager.add_records([pasted, pptx_origin])[0]
    assert item.provider == ProviderId.SHUTTERSTOCK
    assert {origin.source_type for origin in item.occurrences} == {
        SourceType.pasted_text,
        SourceType.slide_text,
    }


def test_state_machine_main_flow_and_attempt_counter() -> None:
    manager, item_id = make_manager_with("https://elements.envato.com/item-ABC1234")
    assert manager.get_item(item_id).state == QueueState.READY
    processing = manager.start_processing(item_id)
    assert processing.state == QueueState.PROCESSING
    assert processing.attempt_count == 1
    completed = manager.mark_completed(item_id)
    assert completed.state == QueueState.COMPLETED
    assert completed.attempt_count == 1


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (QueueState.PENDING, QueueState.READY),
        (QueueState.PENDING, QueueState.BLOCKED),
        (QueueState.READY, QueueState.PROCESSING),
        (QueueState.PROCESSING, QueueState.COMPLETED),
        (QueueState.PROCESSING, QueueState.FAILED),
        (QueueState.FAILED, QueueState.READY),
    ],
)
def test_all_documented_state_transitions_are_allowed(
    current: QueueState, target: QueueState
) -> None:
    validate_transition(current, target)


def test_failure_error_is_recorded_and_retry_archives_error() -> None:
    manager, item_id = make_manager_with("https://elements.envato.com/item-ABC1234")
    manager.start_processing(item_id)
    error = QueueError(
        code="provider_timeout",
        safe_message="Provider did not respond.",
        provider=ProviderId.ENVATO,
        retryable=True,
        technical_detail="internal timeout detail",
    )
    failed = manager.mark_failed(item_id, error)
    assert failed.error == error
    assert failed.error.code == "provider_timeout"
    assert failed.error.safe_message == "Provider did not respond."
    assert failed.error.retryable is True

    ready = manager.prepare_retry(item_id)
    assert ready.state == QueueState.READY
    assert ready.error is None
    assert ready.error_history == (error,)
    processing = manager.start_processing(item_id)
    assert processing.attempt_count == 2


@pytest.mark.parametrize(
    ("url", "first_action", "target"),
    [
        (
            "https://elements.envato.com/item-ABC1234",
            None,
            QueueState.COMPLETED,
        ),
        (
            "https://elements.envato.com/item-ABC1234",
            "complete",
            QueueState.PROCESSING,
        ),
        (
            "https://example.com/unsupported",
            None,
            QueueState.COMPLETED,
        ),
    ],
)
def test_invalid_transitions_are_rejected(
    url: str, first_action: str | None, target: QueueState
) -> None:
    manager, item_id = make_manager_with(url)
    if first_action == "complete":
        manager.start_processing(item_id)
        manager.mark_completed(item_id)
    with pytest.raises(InvalidTransitionError):
        manager.transition(item_id, target)


def test_processing_to_failed_requires_error() -> None:
    manager, item_id = make_manager_with("https://elements.envato.com/item-ABC1234")
    manager.start_processing(item_id)
    with pytest.raises(QueueInvariantError):
        manager.transition(item_id, QueueState.FAILED)


def test_summary_empty_and_multiple_states_and_provider_counts() -> None:
    manager = QueueManager()
    empty = manager.summary()
    assert empty.total == 0
    assert empty.progress_fraction == 0.0
    assert all(count == 0 for count in empty.provider_counts.values())

    urls = [
        "https://elements.envato.com/item-ABC1234",
        "https://plataformaa.assetway.com.br/p/acervo/search?assetId=77",
        "https://example.com/unknown",
    ]
    items = manager.add_records([make_record(url) for url in urls])
    manager.start_processing(items[0].item_id)
    manager.mark_completed(items[0].item_id)
    summary = manager.summary()
    assert (summary.total, summary.ready, summary.completed, summary.blocked) == (3, 1, 1, 1)
    assert summary.provider_counts[ProviderId.ENVATO] == 1
    assert summary.provider_counts[ProviderId.ASSETWAY] == 1
    assert summary.provider_counts[ProviderId.UNKNOWN] == 1
    assert summary.progress_fraction == pytest.approx(2 / 3)
    assert manager.filter_by_state(QueueState.READY)[0].item_id == items[1].item_id
    assert [item.state for item in manager.list_items(QueueState.BLOCKED)] == [QueueState.BLOCKED]


def test_removal_keeps_indexes_consistent_and_rejects_processing_item() -> None:
    manager, item_id = make_manager_with("https://elements.envato.com/item-ABC1234")
    manager.start_processing(item_id)
    with pytest.raises(QueueInvariantError):
        manager.remove_item(item_id)
    manager.mark_failed(
        item_id,
        QueueError("failed", "Safe failure.", ProviderId.ENVATO, retryable=False),
    )
    removed = manager.remove_item(item_id)
    assert removed.item_id == item_id
    assert manager.get_by_url(removed.normalized_url) is None
    assert manager.summary().total == 0


def test_multiple_origins_from_input_record_are_kept() -> None:
    url = "https://elements.envato.com/item-ABC1234"
    first = make_record(url, SourceType.pasted_text)
    second = make_record(url, SourceType.slide_text)
    combined = UrlRecord.from_occurrences([*first.occurrences, *second.occurrences])
    item = QueueManager().add_records([combined])[0]
    assert len(item.occurrences) == 2