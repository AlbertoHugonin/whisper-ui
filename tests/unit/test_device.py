from __future__ import annotations

import logging
from unittest.mock import patch

from whisper_ui.core.device import (
    detect_device,
    validate_compute_type,
)


class TestDetectDevice:
    def test_auto_with_cuda(self):
        with patch("whisper_ui.core.device._cuda_available", return_value=True):
            assert detect_device("auto") == "cuda"

    def test_auto_without_cuda(self):
        with patch("whisper_ui.core.device._cuda_available", return_value=False):
            assert detect_device("auto") == "cpu"

    def test_auto_with_rocm(self):
        with (
            patch("whisper_ui.core.device._cuda_available", return_value=False),
            patch("whisper_ui.core.device._rocm_available", return_value=True),
        ):
            assert detect_device("auto") == "rocm"

    def test_explicit_rocm_unavailable(self, caplog):
        with (
            patch("whisper_ui.core.device._rocm_available", return_value=False),
            caplog.at_level(logging.WARNING),
        ):
            result = detect_device("rocm")
        assert result == "cpu"
        assert "ROCm requested but not available" in caplog.text


class TestValidateComputeType:
    def test_cpu_float16_downgrades(self, caplog):
        with caplog.at_level(logging.WARNING):
            result = validate_compute_type("cpu", "float16")
        assert result == "int8"
        assert "not supported on CPU" in caplog.text
