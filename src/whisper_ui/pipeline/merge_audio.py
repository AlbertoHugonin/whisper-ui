"""Safely concatenate ordered, heterogeneous audio uploads before transcription.

Runs on an I/O worker, not in the web process or browser. ffmpeg streams
input audio and normalises to mono 16 kHz PCM so timestamps are continuous.
The source parts and manifest remain on disk for reproducible retries.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from whisper_ui.core.constants import MAX_BATCH_SIZE

if TYPE_CHECKING:
    from whisper_ui.pipeline.base import ProgressCallback

logger = logging.getLogger(__name__)
MANIFEST_NAME = "merge-manifest.json"
_FILENAME_PATTERN = re.compile(r"part-\d{4}\.[a-z0-9]{2,8}\Z")


class AudioMergeStage:
    name = "merge_audio"

    def execute(self, context: dict[str, Any], on_progress: ProgressCallback | None = None) -> dict[str, Any]:
        output = Path(context["input_path"])
        manifest = Path(context["merge_manifest_path"])
        if manifest.parent != output.parent or not manifest.is_file():
            raise ValueError("Ordered audio manifest missing or outside job directory")
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        parts = payload.get("parts") if isinstance(payload, dict) and payload.get("version") == 1 else None
        if not isinstance(parts, list) or not 2 <= len(parts) <= MAX_BATCH_SIZE:
            raise ValueError("Expected 2 or more ordered audio parts")
        paths: list[Path] = []
        for part in parts:
            filename = part.get("filename") if isinstance(part, dict) else None
            if not isinstance(filename, str) or not _FILENAME_PATTERN.fullmatch(filename):
                raise ValueError("Invalid audio part name in manifest")
            path = manifest.parent / filename
            if path.is_symlink() or not path.is_file():
                raise ValueError("Audio part missing or invalid")
            paths.append(path)
        if len(set(paths)) != len(paths):
            raise ValueError("Repeated audio part in manifest")
        if on_progress:
            on_progress(0.05, "Unione audio nell'ordine scelto")
        # concat's filter decodes each input according to its actual codec,
        # unlike the concat demuxer which would require equal containers.
        argv = ["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y"]
        for path in paths:
            argv += ["-i", str(path)]
        filters = [
            f"[{idx}:a:0]aresample=16000,aformat=sample_fmts=s16:channel_layouts=mono[a{idx}]"
            for idx in range(len(paths))
        ]
        filters.append(
            "".join(f"[a{idx}]" for idx in range(len(paths)))
            + f"concat=n={len(paths)}:v=0:a=1[combined]"
        )
        temp = output.with_name(f".{output.stem}-{uuid.uuid4().hex}.wav")
        try:
            result = subprocess.run(
                [*argv, "-filter_complex", ";".join(filters), "-map", "[combined]",
                        "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(temp)],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=7200, check=False,
            )
            if result.returncode != 0 or not temp.is_file() or temp.stat().st_size < 44:
                logger.warning("ffmpeg audio merge failed: %s", result.stderr[-1200:].decode("utf-8", "replace"))
                raise ValueError("Impossibile unire i file audio: verifica che ogni file contenga una traccia audio")
            temp.replace(output)
        finally:
            temp.unlink(missing_ok=True)
        if on_progress:
            on_progress(1.0, "Audio unito e pronto per Whisper")
        context["input_path"] = str(output)
        return context

    def cleanup(self) -> None:
        pass
