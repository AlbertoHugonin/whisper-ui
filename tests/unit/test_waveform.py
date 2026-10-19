"""Peak extraction must work for multi-hour audio without buffering the media."""
from array import array
from pathlib import Path
import wave

import pytest

from whisper_ui.web.waveform import cached_peaks, peaks


def test_waveform_real_pcm_amplitude_and_cache(tmp_path: Path):
    p = tmp_path / "processed.wav"
    frames = array('h', [0] * 2000 + [16000] * 2000 + [-24000] * 2000 + [0] * 2000)
    with wave.open(str(p), 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(frames.tobytes())
    values = peaks(p, 80)
    assert len(values) == 80
    assert max(values[:15]) == 0
    assert 0.45 < max(values[20:34]) < 0.6
    assert 0.65 < max(values[45:65]) < 0.8
    stat = p.stat()
    assert cached_peaks(str(p), stat.st_mtime_ns, stat.st_size) == cached_peaks(str(p), stat.st_mtime_ns, stat.st_size)


def test_waveform_rejects_invalid_request(tmp_path: Path):
    with pytest.raises(ValueError):
        peaks(tmp_path / 'missing.wav', 3000)
