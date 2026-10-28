from __future__ import annotations

import asyncio
import json
import secrets
import subprocess
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response

from whisper_ui.core.constants import VIEWER_SEARCH_SEGMENT_LIMIT
from whisper_ui.core.models import JobStatus
from whisper_ui.export.factory import get_exporter
from whisper_ui.ui import labels as ui_labels
from whisper_ui.web.auth import owner_filter
from whisper_ui.web.deps import (
    CurrentUserDep,
    DbDep,
    FileStoreDep,
    RedisDep,
    SettingsDep,
    make_content_disposition,
    templates,
)
from whisper_ui.web.validation import validate_hex_id
from whisper_ui.web.waveform import cached_peaks

_MEDIA_MIME_TYPES: dict[str, str] = {
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".flac": "audio/flac",
    ".wma": "audio/x-ms-wma",
    ".opus": "audio/opus",
    ".ogg": "audio/ogg",
    ".wav": "audio/wav",
}

_VIDEO_EXTENSIONS = {".mp4", ".webm", ".mkv"}

router = APIRouter()

_SPEAKER_COLOR_CLASSES = [
    "speaker-1",
    "speaker-2",
    "speaker-3",
    "speaker-4",
    "speaker-5",
    "speaker-6",
    "speaker-7",
    "speaker-8",
]


def _build_speaker_colors(segments: list) -> dict[str, str]:
    """Assign a color CSS class to each unique speaker."""
    colors: dict[str, str] = {}
    for seg in segments:
        speaker = getattr(seg, "speaker", None)
        if speaker and speaker not in colors:
            idx = len(colors) % len(_SPEAKER_COLOR_CLASSES)
            colors[speaker] = _SPEAKER_COLOR_CLASSES[idx]
    return colors


@router.get("/viewer", response_class=HTMLResponse)
async def viewer_redirect():
    return RedirectResponse("/jobs", status_code=302)


@router.get("/viewer/{job_id}", response_class=HTMLResponse)
async def viewer_page(
    request: Request, db: DbDep, filestore: FileStoreDep, user: CurrentUserDep,
    job_id: str, settings: SettingsDep,
):
    validate_hex_id(job_id, "job_id")

    job = None
    result = None
    error = None
    speaker_colors: dict[str, str] = {}

    job = db.get_job(job_id, owner_id=owner_filter(user))
    if job is None:
        error = "not_found"
    elif job.status != JobStatus.COMPLETED:
        error = "not_completed"
    else:
        result = await asyncio.to_thread(filestore.load_result, job_id)
        if result is None:
            error = "no_result"
        else:
            speaker_colors = _build_speaker_colors(result.segments)

    corrected_result = await asyncio.to_thread(filestore.load_corrected_result, job_id) if result else None
    if corrected_result and len(corrected_result.segments) != len(result.segments):
        corrected_result = None
    reading_paragraphs = []
    if result:
        segs = result.segments
        first = 0
        for idx in range(1, len(segs) + 1):
            if idx < len(segs):
                prev, cur = segs[idx - 1], segs[idx]
                current_text = " ".join(s.text for s in segs[first:idx])
                stop = (prev.speaker != cur.speaker or cur.start - prev.end > 3
                        or len(current_text) + len(cur.text) > 360
                        or (len(current_text) > 165 and current_text.rstrip().endswith((".", "?", "!"))))
                if not stop:
                    continue
            block = segs[first:idx]
            if block:
                original = " ".join(s.text.strip() for s in block if s.text.strip())
                corrected = (" ".join(s.text.strip() for s in corrected_result.segments[first:idx] if s.text.strip())
                             if corrected_result else original)
                reading_paragraphs.append((block[0].start, block[0].speaker, original, corrected))
            first = idx
    search_disabled = result is not None and len(result.segments) > VIEWER_SEARCH_SEGMENT_LIMIT
    # The Download Media button is only useful when the URL job's
    # downloaded media file is still on disk. Retention can reclaim it
    # while the transcript stays — checking here keeps the template free
    # of file-system access and avoids the 404 dead-click the button
    # would otherwise produce.
    media_path = filestore.get_any_media_path(job_id, job.filepath if job else None) if job is not None else None
    original_available = media_path is not None
    media_available = original_available
    media_is_video = media_available and media_path.suffix.lower() in _VIDEO_EXTENSIONS
    processed_path = filestore.get_output_artifact(job_id, "processed.wav") if job is not None else None
    processed_available = processed_path is not None
    media_available = original_available or processed_available
    analysis = None
    analysis_path = filestore.get_output_artifact(job_id, "audio-analysis.json") if job is not None else None
    if analysis_path is not None:
        try:
            analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            analysis = None

    return templates.TemplateResponse(
        request=request,
        name="viewer.html",
        context={
            "active_page": "jobs",
            "job": job,
            "result": result,
            "corrected_result": corrected_result,
            "correction_available": settings.llm_correction_available,
            "reading_paragraphs": reading_paragraphs,
            "error": error,
            "speaker_colors": speaker_colors,
            "search_disabled": search_disabled,
            "media_available": media_available,
            "original_available": original_available,
            "initial_media_source": "original" if original_available else "processed",
            "media_is_video": media_is_video,
            "processed_available": processed_available,
            "audio_analysis": analysis,
        },
    )


@router.post("/viewer/{job_id}/correct")
async def request_correction(job_id: str, db: DbDep, redis: RedisDep, settings: SettingsDep,
                             user: CurrentUserDep, llm_context_prompt: Annotated[str, Form()] = ""):
    validate_hex_id(job_id, "job_id")
    job = db.get_job(job_id, owner_id=owner_filter(user))
    if job is None or job.status != JobStatus.COMPLETED:
        raise HTTPException(status_code=404)
    if not settings.llm_correction_available:
        raise HTTPException(status_code=503, detail="LLM correction unavailable")
    from whisper_ui.worker.standalone_correction import correction_status, enqueue_correction
    if correction_status(redis, job_id)["status"] not in {"queued", "started", "deferred", "scheduled"}:
        job.llm_context_prompt = llm_context_prompt.strip()[:4000]
        db.update_job(job)
        enqueue_correction(redis, job_id)
    return RedirectResponse(f"/viewer/{job_id}", status_code=303)


@router.get("/viewer/{job_id}/correction-status")
async def get_correction_status(job_id: str, db: DbDep, redis: RedisDep, user: CurrentUserDep):
    validate_hex_id(job_id, "job_id")
    if db.get_job(job_id, owner_id=owner_filter(user)) is None:
        raise HTTPException(status_code=404)
    from whisper_ui.worker.standalone_correction import correction_status
    return JSONResponse(correction_status(redis, job_id))


@router.get("/viewer/{job_id}/export/{format_name}")
async def export_download(
    job_id: str, format_name: str, db: DbDep, filestore: FileStoreDep,
    user: CurrentUserDep, version: str = "original",
):
    validate_hex_id(job_id, "job_id")
    job = db.get_job(job_id, owner_id=owner_filter(user))
    if job is None or job.status != JobStatus.COMPLETED:
        raise HTTPException(status_code=404)

    if version not in {"original", "corrected"}:
        raise HTTPException(status_code=400, detail="Invalid transcript version")
    loader = filestore.load_corrected_result if version == "corrected" else filestore.load_result
    result = await asyncio.to_thread(loader, job_id)
    if result is None:
        raise HTTPException(status_code=404)

    try:
        exporter = get_exporter(format_name)
    except ValueError:
        # Fixed message: do not echo the caller-supplied format name back.
        raise HTTPException(status_code=400, detail="Unsupported export format") from None
    # Offload the export like the batch path (jobs.py create_batch_zip): a DOCX
    # or a very long transcript is CPU-bound (python-docx builds the whole XML
    # tree) and would otherwise block the single event loop for seconds.
    data = await asyncio.to_thread(exporter.export, result)
    suffix = "-corrected" if version == "corrected" else ""
    filename = f"{Path(job.filename).stem}{suffix}{exporter.file_extension}"

    return Response(
        content=data,
        media_type=exporter.mime_type,
        headers={"Content-Disposition": make_content_disposition(filename)},
    )


@router.get("/viewer/{job_id}/media")
async def media_download(job_id: str, db: DbDep, filestore: FileStoreDep, user: CurrentUserDep):
    validate_hex_id(job_id, "job_id")
    job = db.get_job(job_id, owner_id=owner_filter(user))
    if job is None:
        raise HTTPException(status_code=404)

    media_path = filestore.get_any_media_path(job_id, job.filepath)
    if media_path is None or not media_path.is_file():
        raise HTTPException(status_code=404)

    ext = media_path.suffix.lower()
    mime = _MEDIA_MIME_TYPES.get(ext, "application/octet-stream")
    filename = f"{Path(job.filename).stem}{ext}"

    return FileResponse(
        path=media_path,
        media_type=mime,
        headers={"Content-Disposition": make_content_disposition(filename, "inline")},
    )




@router.get("/viewer/{job_id}/waveform")
async def media_waveform(
    job_id: str, db: DbDep, filestore: FileStoreDep, user: CurrentUserDep,
    source: str = "original",
):
    """Authenticated peaks of the SELECTED audio track (never both at once)."""
    validate_hex_id(job_id, "job_id")
    if source not in ("original", "processed"):
        raise HTTPException(status_code=400, detail="Unknown audio source")
    job = db.get_job(job_id, owner_id=owner_filter(user))
    if job is None or job.status != JobStatus.COMPLETED:
        raise HTTPException(status_code=404)
    if source == "processed":
        path = filestore.get_output_artifact(job_id, "processed.wav")
    else:
        path = filestore.get_any_media_path(job_id, job.filepath)
    if path is None:
        raise HTTPException(status_code=404)
    try:
        stat = await asyncio.to_thread(path.stat)
        values = await asyncio.wait_for(
            asyncio.to_thread(cached_peaks, str(path), stat.st_mtime_ns, stat.st_size),
            timeout=75,
        )
    except (OSError, ValueError, EOFError, TimeoutError, RuntimeError, subprocess.SubprocessError):
        raise HTTPException(status_code=422, detail="Waveform unavailable") from None
    return Response(
        content=json.dumps({"peaks": values, "source": source}),
        media_type="application/json",
        headers={"Cache-Control": "private, max-age=600"},
    )


@router.get("/viewer/{job_id}/processed-audio")
async def processed_audio(
    job_id: str, db: DbDep, filestore: FileStoreDep, user: CurrentUserDep, download: bool = False,
):
    validate_hex_id(job_id, "job_id")
    job = db.get_job(job_id, owner_id=owner_filter(user))
    if job is None:
        raise HTTPException(status_code=404)
    path = filestore.get_output_artifact(job_id, "processed.wav")
    if path is None:
        raise HTTPException(status_code=404)
    return FileResponse(
        path=path,
        media_type="audio/wav",
        headers={
            "Content-Disposition": make_content_disposition(
                f"{Path(job.filename).stem}.processed.wav", "attachment" if download else "inline"
            )
        },
    )


@router.post("/viewer/{job_id}/share")
async def share_create(job_id: str, db: DbDep, user: CurrentUserDep):
    validate_hex_id(job_id, "job_id")
    job = db.get_job(job_id, owner_id=owner_filter(user))
    if job is None or job.status != JobStatus.COMPLETED:
        raise HTTPException(status_code=404)

    if not job.share_token:
        job.share_token = secrets.token_urlsafe(16)
        db.update_job(job)

    return Response(
        status_code=200,
        headers={
            "HX-Trigger": (
                '{"showToast": {"message": ' + json.dumps(ui_labels.VIEWER_SHARE_LINK_CREATED) + ', "type": "success"}}'
            ),
            "HX-Refresh": "true",
        },
    )


@router.delete("/viewer/{job_id}/share")
async def share_revoke(job_id: str, db: DbDep, user: CurrentUserDep):
    validate_hex_id(job_id, "job_id")
    job = db.get_job(job_id, owner_id=owner_filter(user))
    if job is None:
        raise HTTPException(status_code=404)

    if job.share_token:
        job.share_token = None
        db.update_job(job)

    return Response(
        status_code=200,
        headers={"HX-Refresh": "true"},
    )
