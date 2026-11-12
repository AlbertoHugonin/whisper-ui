"""Full-band audio envelopes, with bounded memory and decoder wall time."""

from __future__ import annotations

import json
import math
import subprocess
import sys
import wave
from array import array
from functools import lru_cache
from pathlib import Path

POINTS = 1200


def peaks(path: Path, points: int = POINTS) -> list[float]:
    if not 1 <= points <= 2400:
        raise ValueError("Invalid peak count")
    with wave.open(str(path), "rb") as audio:
        if audio.getcomptype() != "NONE" or audio.getsampwidth() != 2:
            raise ValueError("Only uncompressed 16-bit WAV is supported")
        frames, channels = audio.getnframes(), audio.getnchannels()
        if not frames or channels not in (1, 2):
            return []
        output = []
        for index in range(points):
            begin = index * frames // points
            end = min(frames, max(begin + 1, (index + 1) * frames // points))
            audio.setpos(begin)
            remaining, peak = end - begin, 0
            while remaining:
                count = min(65536, remaining)
                samples = array("h")
                samples.frombytes(audio.readframes(count))
                if sys.byteorder != "little":
                    samples.byteswap()
                if not samples:
                    break
                peak = max(peak, max(samples), -min(samples))
                remaining -= len(samples) // channels
            output.append(round(min(1.0, peak / 32768.0), 4))
        return output


def compressed_peaks(path: Path, points: int = POINTS) -> list[float]:
    if not 1 <= points <= 2400:
        raise ValueError("Invalid peak count")
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=sample_rate:format=duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=12,
    )
    info = json.loads(probe.stdout)
    try:
        seconds = float(info["format"]["duration"])
        rate = int(info["streams"][0]["sample_rate"])
    except (KeyError, IndexError, TypeError) as error:
        raise ValueError("No decodable audio stream") from error
    if not math.isfinite(seconds) or not 0 < seconds <= 12 * 3600 or not 0 < rate <= 384000:
        raise ValueError("Invalid audio length or sample rate")
    # Measure every sample at the ORIGINAL rate, in ffmpeg, before reducing
    # to a small envelope. Resampling to 800 Hz erased speech/high frequencies.
    # Keep channels separate: downmixing can cancel opposite-phase signals.
    block = max(1, math.ceil(seconds * rate / points))
    decoded = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-vn",
            "-af",
            f"asetnsamples=n={block}:p=1,astats=metadata=1:reset=1,"
            "ametadata=print:key=lavfi.astats.Overall.Peak_level:file=-",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=55,
    )
    output = [0.0] * points
    index = 0
    for line in decoded.stdout.splitlines():
        if line.startswith("frame:"):
            timestamp = float(line.rsplit("pts_time:", 1)[1])
            index = min(points - 1, int(timestamp / seconds * points + 1e-5))
        elif line.startswith("lavfi.astats.Overall.Peak_level="):
            db = float(line.split("=", 1)[1])
            amplitude = min(1.0, 10 ** (db / 20)) if math.isfinite(db) else 0.0
            output[index] = max(output[index], round(amplitude, 4))
    return output


@lru_cache(maxsize=32)
def cached_peaks(path: str, modified_ns: int, size: int) -> tuple[float, ...]:
    source = Path(path)
    try:
        return tuple(peaks(source)) if source.suffix.lower() == ".wav" else tuple(compressed_peaks(source))
    except (wave.Error, EOFError, ValueError):
        if source.suffix.lower() == ".wav":
            return tuple(compressed_peaks(source))
        raise
