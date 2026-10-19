"""Bounded, true audio-peak overview for the sticky transcription timeline.

Uses short random-access reads from the existing processed PCM WAV. Never
loads an entire multi-hour recording into memory or retains user audio data.
"""

from __future__ import annotations

from array import array
from functools import lru_cache
from pathlib import Path
import sys
import wave

POINTS = 1200
WINDOW_FRAMES = 512


def peaks(path: Path, points: int = POINTS) -> list[float]:
    if not 1 <= points <= 2400:
        raise ValueError("Invalid peak count")
    with wave.open(str(path), "rb") as source:
        if source.getcomptype() != "NONE" or source.getsampwidth() != 2:
            raise ValueError("Only uncompressed 16-bit WAV is supported")
        total = source.getnframes()
        channels = source.getnchannels()
        if not total or not 1 <= channels <= 2:
            return []
        result = []
        # Each of at most 1200 buckets reads at most 512 frames (constant RAM).
        # The center sample is taken at regular intervals throughout the file.
        for i in range(points):
            start = min(total - 1, (i * total) // points)
            window = min(WINDOW_FRAMES, total - start)
            source.setpos(start)
            sample = array("h")
            sample.frombytes(source.readframes(window))
            if sys.byteorder != "little":
                sample.byteswap()
            amplitude = max((abs(v) for v in sample), default=0) / 32768.0
            result.append(round(min(1.0, amplitude), 4))
        return result


@lru_cache(maxsize=48)
def cached_peaks(path: str, modified_ns: int, size: int) -> tuple[float, ...]:
    """Filename and stat fingerprint; route checks user ownership first."""
    return tuple(peaks(Path(path)))
