from __future__ import annotations

import time

from image_downloader.input import SourceType, UrlOccurrence, UrlRecord
from image_downloader.providers import (
    ProviderId,
    ProviderRegistry,
    classify_url,
    classify_url_records,
)


def test_assetway_primary_url_detects_provider_and_asset_reference() -> None:
    match = classify_url("https://plataformaa.assetway.com.br/p/acervo/search?modal=asset&assetId=65507")
    assert match.provider == ProviderId.ASSETWAY
    assert match.asset_reference == "65507"
    assert match.hostname == "plataformaa.assetway.com.br"


def test_assetway_without_asset_id_is_still_assetway() -> None:
    match = classify_url("https://i.assetway.com.br/image/asset")
    assert match.provider == ProviderId.ASSETWAY
    assert match.asset_reference is None


def test_assetway_false_domain_is_unknown() -> None:
    match = classify_url("https://plataformaa.assetway.com.br.evil.example/asset")
    assert match.provider == ProviderId.UNKNOWN


def test_shutterstock_known_and_unknown_paths_are_detected() -> None:
    assert (
        classify_url("https://www.shutterstock.com/image-photo/landscape-123456789")
        .provider
        == ProviderId.SHUTTERSTOCK
    )
    assert (
        classify_url("https://www.shutterstock.com/image-vector/pattern-987654321")
        .provider
        == ProviderId.SHUTTERSTOCK
    )
    assert (
        classify_url("https://www.shutterstock.com/image-illustration/design-456789")
        .provider
        == ProviderId.SHUTTERSTOCK
    )
    assert (
        classify_url("https://www.shutterstock.com/search/forest").provider
        == ProviderId.SHUTTERSTOCK
    )
    assert (
        classify_url("https://shutterstock.com.evil.example/asset").provider
        == ProviderId.UNKNOWN
    )


def test_envato_elements_item_and_case_variants_are_detected() -> None:
    match = classify_url("https://elements.envato.com/example-item-ABC1234")
    assert match.provider == ProviderId.ENVATO
    assert match.asset_reference == "ABC1234"

    localized = classify_url("https://elements.envato.com/pt-br/example-item-ABC1234")
    assert localized.provider == ProviderId.ENVATO
    assert localized.asset_reference == "ABC1234"

    unknown = classify_url("https://elements.envato.com/pt-br/example-item")
    assert unknown.provider == ProviderId.ENVATO
    assert unknown.asset_reference is None

    fake = classify_url("https://elements.envato.com.evil.example/example-item-ABC1234")
    assert fake.provider == ProviderId.UNKNOWN

    app_item = classify_url(
        "https://app.envato.com/photos/3e33fbad-d417-4368-9778-8c89c416cbf1"
    )
    assert app_item.provider == ProviderId.ENVATO
    assert app_item.asset_reference == "3e33fbad-d417-4368-9778-8c89c416cbf1"

    fake_app = classify_url(
        "https://app.envato.com.evil.example/photos/3e33fbad-d417-4368-9778-8c89c416cbf1"
    )
    assert fake_app.provider == ProviderId.UNKNOWN


def test_unknown_and_security_hostname_cases() -> None:
    assert classify_url("https://example.com/asset/123").provider == ProviderId.UNKNOWN
    assert classify_url("https://fake-shutterstock.com/asset").provider == ProviderId.UNKNOWN
    assert classify_url("https://envato.example/asset").provider == ProviderId.UNKNOWN
    assert classify_url("https://assetway.example/asset").provider == ProviderId.UNKNOWN
    assert classify_url("https://www.amazon.com/asset").provider == ProviderId.UNKNOWN


def test_registry_classifies_collection_and_preserves_origins() -> None:
    record = UrlRecord(
        normalized_url="https://www.shutterstock.com/image-photo/landscape-123456789",
        original_url="https://www.shutterstock.com/image-photo/landscape-123456789",
        occurrences=(
            UrlOccurrence(
                original_url="https://www.shutterstock.com/image-photo/landscape-123456789",
                normalized_url="https://www.shutterstock.com/image-photo/landscape-123456789",
                source_type=SourceType.pasted_text,
                source_name="pasted_text",
                file_name=None,
                slide_number=None,
                location=None,
                context="pasted",
            ),
            UrlOccurrence(
                original_url="https://www.shutterstock.com/image-photo/landscape-123456789",
                normalized_url="https://www.shutterstock.com/image-photo/landscape-123456789",
                source_type=SourceType.slide_text,
                source_name="slide1.xml",
                file_name="demo.pptx",
                slide_number=1,
                location="ppt/slides/slide1.xml",
                context="slide",
            ),
        ),
    )
    classified = classify_url_records([record])
    assert len(classified) == 1
    assert classified[0].record.normalized_url == record.normalized_url
    assert classified[0].provider_match.provider == ProviderId.SHUTTERSTOCK
    assert len(classified[0].record.occurrences) == 2


def test_registry_supports_case_insensitive_hosts_and_queries() -> None:
    assert (
        classify_url(
            "HTTPS://WWW.SHUTTERSTOCK.COM/image-photo/landscape-123456789?foo=bar"
        ).provider
        == ProviderId.SHUTTERSTOCK
    )
    assert (
        classify_url(
            "https://plataformaa.assetway.com.br/p/acervo/search?modal=asset"
            "&assetid=77#frag"
        ).provider
        == ProviderId.ASSETWAY
    )
    assert (
        classify_url("https://example.com/image/123?x=1#frag").provider
        == ProviderId.UNKNOWN
    )


def test_registry_local_classification_metrics_for_collection() -> None:
    urls = [
        "https://plataformaa.assetway.com.br/p/acervo/search?modal=asset&assetId=65507",
        "https://www.shutterstock.com/image-photo/landscape-123456789",
        "https://elements.envato.com/example-item-ABC1234",
        "https://example.com/image/123",
    ]
    registry = ProviderRegistry()
    start = time.perf_counter()
    results = registry.classify_many(urls)
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert len(results) == 4
    assert sum(1 for item in results if item.provider == ProviderId.ASSETWAY) == 1
    assert sum(1 for item in results if item.provider == ProviderId.SHUTTERSTOCK) == 1
    assert sum(1 for item in results if item.provider == ProviderId.ENVATO) == 1
    assert sum(1 for item in results if item.provider == ProviderId.UNKNOWN) == 1
    assert elapsed_ms >= 0.0
