"""Log redaction filter."""

from __future__ import annotations

import logging

import pytest

from app.platform.secrets.redaction import MASK, RedactionFilter, SecretRegistry, install_redaction, redact
from app.platform.secrets.store import DbSecretStore


@pytest.mark.parametrize(
    ("text", "leak"),
    [
        ("Authorization: Bearer eyJhbGciOi.payload.sig", "eyJhbGciOi"),
        ("GET /x?api_key=abc123def&page=2", "abc123def"),
        ('{"client_secret": "very-secret-thing", "client_id": "public"}', "very-secret-thing"),
        ("password=hunter22 user=bob", "hunter22"),
        ("X-API-Key: k-998877", "k-998877"),
        ("auth Basic dXNlcjpwYXNz", "dXNlcjpwYXNz"),
    ],
)
def test_credential_patterns_are_masked(text: str, leak: str, registry: SecretRegistry) -> None:
    out = redact(text, registry)
    assert leak not in out and MASK in out


def test_registered_values_are_masked_anywhere(registry: SecretRegistry) -> None:
    registry.register("opaque-value-42")
    registry.register("abc")  # too short: not registered
    assert redact("failed with opaque-value-42 inside", registry) == f"failed with {MASK} inside"
    assert "abc" in redact("abc", registry)
    registry.forget("opaque-value-42")
    assert "opaque-value-42" in redact("opaque-value-42", registry)


def test_filter_masks_message_args_extras_and_exceptions(
    registry: SecretRegistry, caplog: pytest.LogCaptureFixture
) -> None:
    registry.register("zz-top-secret-zz")
    logger = logging.getLogger("tests.redaction")
    flt = RedactionFilter(registry)
    caplog.handler.addFilter(flt)
    try:
        with caplog.at_level(logging.INFO, logger="tests.redaction"):
            logger.info("calling with %s", "zz-top-secret-zz", extra={"header": "Bearer abcdefghijkl"})
            try:
                raise RuntimeError("boom zz-top-secret-zz")
            except RuntimeError:
                logger.exception("failed")
    finally:
        caplog.handler.removeFilter(flt)
    assert "zz-top-secret-zz" not in caplog.text
    assert "abcdefghijkl" not in caplog.records[0].header  # type: ignore[attr-defined]
    assert caplog.records[0].getMessage() == f"calling with {MASK}"


def test_install_is_idempotent_and_covers_handlers(registry: SecretRegistry) -> None:
    logger = logging.getLogger("tests.redaction.install")
    handler = logging.StreamHandler()
    logger.addHandler(handler)
    try:
        first = install_redaction(logger, registry)
        second = install_redaction(logger, registry)
        assert first is second
        assert sum(isinstance(f, RedactionFilter) for f in logger.filters) == 1
        assert sum(isinstance(f, RedactionFilter) for f in handler.filters) == 1
    finally:
        logger.removeHandler(handler)
        logger.filters.clear()


async def test_store_registers_revealed_values(store: DbSecretStore, registry: SecretRegistry, tenant) -> None:  # type: ignore[no-untyped-def]
    await store.put(tenant.id, "k", "value-to-hide-123")
    registry.clear()
    await store.reveal(tenant.id, "k")
    assert redact("leak value-to-hide-123", registry) == f"leak {MASK}"
