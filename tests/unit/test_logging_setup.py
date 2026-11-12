"""Tests for the centralised logging configuration."""

from __future__ import annotations

import json
import logging

import pytest

from whisper_ui.core.logging_setup import (
    JsonFormatter,
    mask_username,
)


def test_mask_username_never_returns_the_full_value():
    secret = "supersecretlogin"
    assert secret not in mask_username(secret)


@pytest.fixture(autouse=True)
def _restore_logging():
    """Reset root logger handlers between tests so dictConfig state does not leak."""
    yield
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)


def test_json_formatter_redacts_sensitive_extra_fields():
    """A call site that accidentally passes a token / password / raw URL via
    extra={} must not leak it: sensitive-named fields are redacted in the JSON
    log while ordinary fields pass through."""
    record = logging.LogRecord("w", logging.INFO, "p", 1, "op", (), None)
    record.hf_token = "hf_realsecret"
    record.password = "hunter2"
    record.redis_url = "redis://:pw@host:6379/0"
    record.api_key = "sk-live-123"
    record.job_id = "abc123"  # ordinary field, must survive

    payload = json.loads(JsonFormatter().format(record))

    assert payload["hf_token"] == "***"
    assert payload["password"] == "***"
    assert payload["redis_url"] == "***"
    assert payload["api_key"] == "***"
    assert payload["job_id"] == "abc123"
