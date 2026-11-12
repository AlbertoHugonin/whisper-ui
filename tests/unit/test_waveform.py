"""Peak extraction must work for multi-hour audio without buffering the media."""

import wave
from array import array
from pathlib import Path

import pytest

from whisper_ui.web.waveform import cached_peaks, peaks


def test_waveform_real_pcm_amplitude_and_cache(tmp_path: Path):
    p = tmp_path / "processed.wav"
    frames = array("h", [0] * 2000 + [16000] * 2000 + [-24000] * 2000 + [0] * 2000)
    with wave.open(str(p), "wb") as w:
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
        peaks(tmp_path / "missing.wav", 3000)


def test_wav_transient_between_previously_sampled_windows(tmp_path: Path):
    path = tmp_path / "transient.wav"
    values = array("h", [0] * 16000)
    values[1800] = -30000
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(values.tobytes())
    assert peaks(path, 1) == [round(30000 / 32768, 4)]


@pytest.mark.integration
@pytest.mark.parametrize("codec,extension", [("libmp3lame", "mp3"), ("aac", "m4a")])
def test_compressed_waveform_keeps_high_frequency_signal(tmp_path: Path, codec, extension):
    import subprocess

    from whisper_ui.web.waveform import compressed_peaks

    path = tmp_path / f"tone.{extension}"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:duration=1",
            "-af",
            "volume=4",
            "-c:a",
            codec,
            str(path),
        ],
        check=True,
        timeout=15,
    )
    values = compressed_peaks(path, 100)
    assert len(values) == 100
    assert max(values) > 0.35
    assert sum(value > 0.3 for value in values) > 80
