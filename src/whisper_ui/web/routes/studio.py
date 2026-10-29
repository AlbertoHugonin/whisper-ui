"""Authenticated side-by-side transcript revision studio.

Compares all re-transcriptions of one recording, provides an immutable source
for each pane, and stores a separate, timing-preserving personal edit.
"""
from __future__ import annotations

import asyncio
import copy
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from whisper_ui.core.models import JobStatus
from whisper_ui.export.factory import get_exporter
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

router = APIRouter()
_VERSIONS = {"original", "corrected", "custom"}


def _family(job_id: str, db: DbDep, user: CurrentUserDep):
    validate_hex_id(job_id, "job_id")
    current = db.get_job(job_id, owner_id=owner_filter(user))
    if current is None or current.status != JobStatus.COMPLETED:
        raise HTTPException(status_code=404)
    root_id = current.source_job_id or current.id
    jobs = []
    rows = db.conn.execute(
        'SELECT id FROM jobs WHERE id=? OR source_job_id=? ORDER BY created_at',
        (root_id, root_id),
    ).fetchall()
    for row in rows:
        candidate = db.get_job(row['id'], owner_id=owner_filter(user))
        if candidate and candidate.status == JobStatus.COMPLETED:
            jobs.append(candidate)
    if not jobs:
        jobs = [current]
    return root_id, jobs


def _version(token: str, allowed_jobs, filestore: FileStoreDep):
    try:
        job_id, kind = token.split('~', 1)
    except ValueError:
        raise HTTPException(status_code=400, detail='Malformed transcript version') from None
    validate_hex_id(job_id, 'job_id')
    if kind not in _VERSIONS or job_id not in {j.id for j in allowed_jobs}:
        raise HTTPException(status_code=404, detail='Transcript version not available')
    loaders = {
        'original': filestore.load_result,
        'corrected': filestore.load_corrected_result,
        'custom': filestore.load_custom_result,
    }
    result = loaders[kind](job_id)
    if result is None:
        raise HTTPException(status_code=404, detail='Transcript version not available')
    return job_id, kind, result


@router.get('/studio/{job_id}', response_class=HTMLResponse)
async def studio_page(
    job_id: str, request: Request, db: DbDep, filestore: FileStoreDep,
    settings: SettingsDep, user: CurrentUserDep,
):
    root, jobs = _family(job_id, db, user)
    versions = []
    for job in jobs:
        for kind, label, loader in (
            ('original', 'Whisper originale', filestore.load_result),
            ('corrected', 'Correzione LLM', filestore.load_corrected_result),
            ('custom', 'Personalizzata', filestore.load_custom_result),
        ):
            if loader(job.id) is not None:
                versions.append({
                    'id': f'{job.id}~{kind}', 'label': f'{job.filename} · {label} '
                    f'(beam {job.whisper_beam_size}, VAD {job.vad_threshold:.2f})',
                })
    if not versions:
        raise HTTPException(status_code=404, detail='No transcripts found')
    audio_job = next((j for j in jobs if j.id == root), jobs[0])
    has_processed = filestore.get_output_artifact(audio_job.id, 'processed.wav') is not None
    has_original = filestore.get_any_media_path(audio_job.id, audio_job.filepath) is not None
    return templates.TemplateResponse(request=request, name='studio.html', context={
        'active_page': 'jobs', 'job': audio_job, 'versions': versions,
        'left_initial': next((v['id'] for v in versions if v['id'] == f'{job_id}~original'), versions[0]['id']),
        'right_initial': next((v['id'] for v in versions if v['id'] == f'{job_id}~corrected'),
                              versions[min(1, len(versions)-1)]['id']),
        'audio_job_id': audio_job.id, 'has_processed': has_processed,
        'has_original': has_original, 'summary_prompt': next(
            (j.summary_prompt for j in jobs if j.id == job_id), ''),
        'llm_available': settings.llm_correction_available,
    })


@router.get('/studio/{job_id}/version/{token}')
async def studio_version(job_id: str, token: str, db: DbDep, filestore: FileStoreDep, user: CurrentUserDep):
    _, jobs = _family(job_id, db, user)
    _, _, result = _version(token, jobs, filestore)
    return JSONResponse(result.to_dict(), headers={'Cache-Control': 'private, no-store'})


@router.post('/studio/{job_id}/custom')
async def studio_save_custom(
    job_id: str, request: Request, db: DbDep, filestore: FileStoreDep, user: CurrentUserDep,
):
    _, jobs = _family(job_id, db, user)
    if int(request.headers.get('content-length', '0') or 0) > 4_000_000:
        raise HTTPException(status_code=413)
    data = await request.json()
    if not isinstance(data, dict):
        raise HTTPException(status_code=422)
    target_id, _, original = _version(str(data.get('source', '')), jobs, filestore)
    revisions = data.get('segments')
    if not isinstance(revisions, list) or len(revisions) != len(original.segments):
        raise HTTPException(status_code=422, detail='Segment count mismatch')
    edited = copy.deepcopy(original)
    for segment, changes in zip(edited.segments, revisions, strict=True):
        if not isinstance(changes, dict):
            raise HTTPException(status_code=422)
        text = changes.get('text')
        speaker = changes.get('speaker')
        if not isinstance(text, str) or len(text) > 12000:
            raise HTTPException(status_code=422, detail='Invalid segment text')
        if speaker is not None and (not isinstance(speaker, str) or len(speaker) > 60):
            raise HTTPException(status_code=422, detail='Invalid speaker')
        segment.text = text
        segment.speaker = speaker or None
    await asyncio.to_thread(filestore.save_custom_result, target_id, edited)
    return JSONResponse({'ok': True, 'version': f'{target_id}~custom'})


@router.get('/studio/{job_id}/custom-export')
async def studio_custom_export(
    job_id: str, token: str, db: DbDep, filestore: FileStoreDep,
    user: CurrentUserDep, format_name: str = 'txt',
):
    _, jobs = _family(job_id, db, user)
    source_id, kind, result = _version(token, jobs, filestore)
    if kind != 'custom':
        raise HTTPException(status_code=400)
    try:
        exporter = get_exporter(format_name)
    except ValueError:
        raise HTTPException(status_code=400) from None
    body = await asyncio.to_thread(exporter.export, result)
    source_job = next(j for j in jobs if j.id == source_id)
    filename = f'{Path(source_job.filename).stem}-personalizzata{exporter.file_extension}'
    return Response(content=body, media_type=exporter.mime_type, headers={
        'Content-Disposition': make_content_disposition(filename),
    })


@router.post('/studio/{job_id}/summary')
async def studio_summarize(
    job_id: str, request: Request, db: DbDep, filestore: FileStoreDep,
    settings: SettingsDep, redis: RedisDep, user: CurrentUserDep,
):
    _, jobs = _family(job_id, db, user)
    if not settings.llm_correction_available:
        raise HTTPException(status_code=503, detail='LLM unavailable')
    if int(request.headers.get('content-length', '0') or 0) > 20_000:
        raise HTTPException(status_code=413)
    data = await request.json()
    if not isinstance(data, dict):
        raise HTTPException(status_code=422)
    source_id, kind, _ = _version(str(data.get('source', '')), jobs, filestore)
    prompt = data.get('prompt', '')
    if not isinstance(prompt, str) or len(prompt) > 4000:
        raise HTTPException(status_code=422, detail='Invalid summary prompt')
    from whisper_ui.worker.standalone_summary import enqueue_summary, summary_status
    if summary_status(redis, source_id)['status'] not in {'queued', 'started', 'deferred', 'scheduled'}:
        source = db.get_job(source_id, owner_id=owner_filter(user))
        source.summary_prompt = prompt.strip()
        db.update_job(source)
    return JSONResponse(enqueue_summary(redis, source_id, prompt.strip(), kind))


@router.get('/studio/{job_id}/summary-status')
async def studio_summary_status(
    job_id: str, db: DbDep, filestore: FileStoreDep, redis: RedisDep, user: CurrentUserDep,
    source_job: str = '',
):
    _, jobs = _family(job_id, db, user)
    if source_job not in {j.id for j in jobs}:
        raise HTTPException(status_code=404)
    from whisper_ui.worker.standalone_summary import summary_status
    response = summary_status(redis, source_job)
    response['summary'] = filestore.load_summary(source_job)
    return JSONResponse(response, headers={'Cache-Control': 'no-store'})
