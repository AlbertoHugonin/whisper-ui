from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

from whisper_ui.core.constants import FFMPEG_CONVERT_TIMEOUT, STDERR_MAX_LENGTH
from whisper_ui.core.exceptions import PreprocessError
from whisper_ui.core.messages import PREPROCESS_CONVERTING, PREPROCESS_DONE
from whisper_ui.pipeline.audio_probe import get_audio_duration_seconds

if TYPE_CHECKING:
    from whisper_ui.pipeline.base import ProgressCallback

logger = logging.getLogger(__name__)
SUPPORTED_EXTENSIONS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".wma", ".aac", ".opus", ".mp4", ".webm", ".mkv"}
_RE_I = re.compile(r"I:\s*(-?[0-9.]+)\s*LUFS")
_RE_LRA = re.compile(r"LRA:\s*([0-9.]+)\s*LU")
_RE_PEAK = re.compile(r"Peak:\s*(-?[0-9.]+)\s*dBFS")


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=FFMPEG_CONVERT_TIMEOUT)


def _analyze(path: Path) -> dict[str, float | None]:
    """Measure speech-oriented level statistics. Failure is non-fatal."""
    try:
        r = _run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128=peak=true", "-f", "null", "-"])
        text = r.stderr or ""
        i = _RE_I.findall(text); lra = _RE_LRA.findall(text); peak = _RE_PEAK.findall(text)
        return {
            "integrated_lufs": float(i[-1]) if i else None,
            "loudness_range_lu": float(lra[-1]) if lra else None,
            "true_peak_dbfs": float(peak[-1]) if peak else None,
        }
    except (OSError, ValueError, subprocess.TimeoutExpired):
        logger.warning("audio analysis failed for %s", path, exc_info=True)
        return {"integrated_lufs": None, "loudness_range_lu": None, "true_peak_dbfs": None}


def _resolved_filters(context: dict[str, Any], before: dict[str, float | None]) -> tuple[list[str], dict[str, Any]]:
    mode = str(context.get("audio_processing", "auto"))
    if mode == "off":
        return [], {"mode": "off"}
    target = max(-24.0, min(-12.0, float(context.get("audio_target_lufs", -16.0))))
    max_gain = max(0.0, min(30.0, float(context.get("audio_max_gain_db", 18.0))))
    hp = max(0, min(180, int(context.get("audio_highpass_hz", 80))))
    denoise = str(context.get("audio_denoise", "auto"))
    comp = str(context.get("audio_compression", "auto"))
    current = before.get("integrated_lufs")
    needed = max(0.0, min(max_gain, target - current)) if isinstance(current, (int, float)) else 6.0
    lra = before.get("loudness_range_lu")
    if denoise == "auto":
        denoise = "light" if needed >= 6.0 else "off"
    if comp == "auto":
        if isinstance(lra, (int, float)) and lra >= 12:
            comp = "strong"
        elif needed >= 10:
            comp = "medium"
        elif needed >= 4:
            comp = "light"
        else:
            comp = "off"
    filters: list[str] = []
    if hp:
        filters.append(f"highpass=f={hp}")
    if denoise == "light": filters.append("afftdn=nf=-45:nr=6:tn=1")
    elif denoise == "medium": filters.append("afftdn=nf=-40:nr=10:tn=1")
    comp_map = {
        "light": "acompressor=threshold=0.125:ratio=2:attack=20:release=180:makeup=1.4",
        "medium": "acompressor=threshold=0.10:ratio=3:attack=15:release=200:makeup=2",
        "strong": "acompressor=threshold=0.08:ratio=4:attack=10:release=250:makeup=2.5",
    }
    if comp in comp_map: filters.append(comp_map[comp])
    # loudnorm raises quiet speech and reduces hot inputs to a common perceived level;
    # linear=true avoids unnecessary dynamic reshaping after the compressor.
    applied_target = max(-70.0, min(target, current + max_gain)) if isinstance(current, (int, float)) else target
    filters.append(f"loudnorm=I={applied_target}:LRA=7:TP=-1.5:linear=true")
    return filters, {"mode": mode, "target_lufs": target, "applied_target_lufs": round(applied_target, 2), "max_gain_db": max_gain, "estimated_gain_needed_db": round(needed, 2), "highpass_hz": hp, "denoise": denoise, "compression": comp}


class PreprocessStage:
    @property
    def name(self) -> str: return "preprocess"

    def execute(self, context: dict[str, Any], on_progress: ProgressCallback | None = None) -> dict[str, Any]:
        input_path = Path(context["input_path"])
        if input_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise PreprocessError(f"Unsupported file format: {input_path.suffix}")
        if on_progress: on_progress(0.0, PREPROCESS_CONVERTING)
        output_path = input_path.with_suffix(".16k.wav")
        before = _analyze(input_path)
        filters, resolved = _resolved_filters(context, before)
        cmd = ["ffmpeg", "-y", "-i", str(input_path)]
        if filters: cmd += ["-af", ",".join(filters)]
        cmd += ["-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(output_path)]
        try: result = _run(cmd)
        except FileNotFoundError as err: raise PreprocessError("FFmpeg not found. Please install FFmpeg.") from err
        except subprocess.TimeoutExpired as err:
            output_path.unlink(missing_ok=True); raise PreprocessError(f"Audio conversion timed out (>{FFMPEG_CONVERT_TIMEOUT}s).") from err
        if result.returncode != 0:
            output_path.unlink(missing_ok=True); raise PreprocessError(f"FFmpeg failed: {result.stderr[:STDERR_MAX_LENGTH]}")
        after = _analyze(output_path)
        duration = get_audio_duration_seconds(output_path, job_id=context.get("parent_job_id")) or 0.0
        report = {"before": before, "after": after, "processing": resolved, "filters": filters}
        report_path = output_path.with_suffix(".analysis.json")
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        if on_progress: on_progress(1.0, PREPROCESS_DONE)
        context["audio_path"] = str(output_path); context["duration"] = duration; context["audio_analysis"] = report
        return context

    def cleanup(self) -> None: pass
