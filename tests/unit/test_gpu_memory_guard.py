import pytest
from whisper_ui.core.exceptions import TranscriptionError
from whisper_ui.core.gpu_memory_guard import MIB, ensure_free_vram, check_rocm_vram_before_transcription


def test_enough_vram_passes():
    ensure_free_vram(6000, lambda: (7200 * MIB, 16 * 1024 * MIB))


def test_low_vram_fails_with_clear_message():
    with pytest.raises(TranscriptionError, match='Memoria GPU insufficiente.*RX 7900 GRE'):
        ensure_free_vram(6000, lambda: (4900 * MIB, 16 * 1024 * MIB))


def test_unavailable_memory_fails_closed():
    with pytest.raises(TranscriptionError, match='Impossibile leggere'):
        ensure_free_vram(6000, lambda: (_ for _ in ()).throw(OSError('ROCm unavailable')))


def test_non_rocm_and_disabled_guard(monkeypatch):
    monkeypatch.setenv('WHISPER_MIN_FREE_VRAM_MIB', '6000')
    check_rocm_vram_before_transcription('cpu')
    monkeypatch.setenv('WHISPER_MIN_FREE_VRAM_MIB', '0')
    check_rocm_vram_before_transcription('rocm')


def test_invalid_threshold_fail_closed(monkeypatch):
    monkeypatch.setenv('WHISPER_MIN_FREE_VRAM_MIB', 'infinite')
    with pytest.raises(TranscriptionError, match='Soglia minima'):
        check_rocm_vram_before_transcription('rocm')
