"""Real waveform peaks from either stored PCM or the original media track.

A frame window is sampled across each PCM bucket; compressed originals are
stream-decoded by ffmpeg at 800 Hz, bounded in memory and wall time.  No audio
is returned to the API and no full-length media is read into RAM.
"""
from __future__ import annotations

from array import array
from functools import lru_cache
from pathlib import Path
import math
import subprocess
import sys
import time
import wave

POINTS = 1200
WINDOW_FRAMES = 384


def peaks(path: Path, points: int = POINTS) -> list[float]:
    if not 1 <= points <= 2400:
        raise ValueError('Invalid peak count')
    with wave.open(str(path), 'rb') as f:
        if f.getcomptype() != 'NONE' or f.getsampwidth() != 2:
            raise ValueError('Only uncompressed 16-bit WAV is supported')
        frames, channels = f.getnframes(), f.getnchannels()
        if not frames or channels not in (1, 2):
            return []
        out = []
        for i in range(points):
            begin = (i * frames) // points
            end = max(begin + 1, ((i + 1) * frames) // points)
            biggest = 0
            # Cover the WHOLE bucket, not just its first 384 frames.
            for k in range(4):
                pos = min(frames - 1, begin + ((end - begin) * k) // 4)
                f.setpos(pos)
                values = array('h')
                values.frombytes(f.readframes(min(WINDOW_FRAMES, frames - pos)))
                if sys.byteorder != 'little':
                    values.byteswap()
                biggest = max(biggest, max((abs(x) for x in values), default=0))
            out.append(round(min(1., biggest / 32768.), 4))
        return out


def compressed_peaks(path: Path, points: int = POINTS) -> list[float]:
    if not 1 <= points <= 2400:
        raise ValueError('Invalid peak count')
    duration = subprocess.run(
        ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
         '-of', 'default=noprint_wrappers=1:nokey=1', str(path)],
        capture_output=True, text=True, check=True, timeout=12,
    )
    seconds = float(duration.stdout.strip())
    if not math.isfinite(seconds) or seconds <= 0 or seconds > 12 * 3600:
        raise ValueError('Invalid audio length')
    rate = 800
    expected = max(1, int(seconds * rate))
    output = [0] * points
    proc = subprocess.Popen(
        ['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-i', str(path),
         '-vn', '-ac', '1', '-ar', str(rate), '-f', 's16le', 'pipe:1'],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    count = 0
    start = time.monotonic()
    try:
        assert proc.stdout is not None
        while True:
            if time.monotonic() - start > 55:
                raise TimeoutError('Audio waveform decoding timed out')
            buf = proc.stdout.read(16384)
            if not buf:
                break
            samples = array('h')
            samples.frombytes(buf)
            if sys.byteorder != 'little':
                samples.byteswap()
            for val in samples:
                index = min(points - 1, (count * points) // expected)
                output[index] = max(output[index], abs(val))
                count += 1
        if proc.wait(timeout=8) != 0:
            raise ValueError('Could not decode audio')
    finally:
        if proc.poll() is None:
            proc.kill()
        if proc.stdout:
            proc.stdout.close()
        proc.wait()
    return [round(min(1., v / 32768.), 4) for v in output]


@lru_cache(maxsize=32)
def cached_peaks(path: str, modified_ns: int, size: int) -> tuple[float, ...]:
    source = Path(path)
    try:
        return tuple(peaks(source)) if source.suffix.lower() == '.wav' else tuple(compressed_peaks(source))
    except (wave.Error, EOFError, ValueError):
        # WAV-encapsulated non-PCM codecs may require ffmpeg instead.
        if source.suffix.lower() == '.wav':
            return tuple(compressed_peaks(source))
        raise
