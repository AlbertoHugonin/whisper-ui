"""Tests for request parameter validation helpers."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from whisper_ui.core.languages import DEFAULT_WHISPER_MODEL
from whisper_ui.web.validation import (
    validate_hex_id,
    validate_upload_options,
)


def test_validate_hex_id_accepts_32_lowercase_hex():
    value = "0123456789abcdef0123456789abcdef"
    assert validate_hex_id(value) == value


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "abc",  # too short
        "0123456789abcdef0123456789abcde",  # 31 chars
        "0123456789abcdef0123456789abcdef0",  # 33 chars
        "0123456789ABCDEF0123456789ABCDEF",  # uppercase rejected
        "0123456789abcdef0123456789abcdeg",  # non-hex char
        "../../../etc/passwd",  # path traversal attempt
    ],
)
def test_validate_hex_id_rejects_malformed(bad):
    with pytest.raises(HTTPException) as exc:
        validate_hex_id(bad, "job_id")
    assert exc.value.status_code == 400
    assert "job_id" in exc.value.detail


class TestValidateUploadOptions:
    def test_rejects_unknown_language_first(self):
        err = validate_upload_options("klingon", DEFAULT_WHISPER_MODEL)
        assert err is not None
        assert err.error_code == "invalid_language"
        assert err.value == "klingon"
        assert "klingon" in err.message

    def test_rejects_unknown_model(self):
        err = validate_upload_options("zh", "not-a-model")
        assert err is not None
        assert err.error_code == "invalid_model"
        assert err.value == "not-a-model"
