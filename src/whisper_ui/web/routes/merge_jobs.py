"""Create new ordered transcripts from two or more completed jobs.

The operation is non-destructive: source jobs are only read, and all heavy
media operations run asynchronously in the dedicated I/O worker queue.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import JSONResponse
from rq import Queue

from whisper_ui.core.constants import WORKER_QUEUE_IO
from whisper_ui.core.models import Job, JobStatus
from whisper_ui.web.deps import CurrentUserDep, DbDep, FileStoreDep, RedisDep  # noqa: TC001
from whisper_ui.web.validation import validate_hex_id
from whisper_ui.worker.join_completed import (
    ALLOWED_VERSIONS,
    MAX_JOINED_JOBS,
    SOURCES_MANIFEST,
    _source_audio,
    _source_result,
    join_completed_jobs,
)

router = APIRouter()


def _ids(csv: str) -> list[str]:
    values = [x.strip() for x in csv.split(',')]
    if not 2 <= len(values) <= MAX_JOINED_JOBS or len(set(values)) != len(values):
        raise HTTPException(status_code=422, detail='Seleziona da 2 a 20 lavori distinti')
    for value in values:
        validate_hex_id(value, 'job_id')
    return values


def _sources(db, filestore, owner_id, job_ids):
    jobs = []
    for job_id in job_ids:
        item = db.get_job(job_id, owner_id=owner_id)
        if item is None or item.status != JobStatus.COMPLETED:
            raise HTTPException(status_code=404, detail='Un lavoro selezionato non è disponibile')
        if filestore.load_result(job_id) is None:
            raise HTTPException(status_code=422, detail='Una trascrizione non ha risultati da unire')
        jobs.append(item)
    return jobs


@router.get('/jobs/merge/options')
async def merge_job_options(job_ids: str, db: DbDep, filestore: FileStoreDep, user: CurrentUserDep):
    ids = _ids(job_ids)
    jobs = _sources(db, filestore, user.id, ids)
    versions = {'original': 'Originale', 'corrected': 'Corretta LLM', 'custom': 'Personalizzata'}
    return JSONResponse({'jobs': [
        {
            'id': job.id, 'filename': job.filename, 'duration': job.duration,
            'has_audio': _source_audio(filestore, job) is not None,
            'versions': [
                {'value': kind, 'label': description}
                for kind, description in versions.items()
                if _source_result(filestore, job.id, kind) is not None
            ],
        }
        for job in jobs
    ]}, headers={'Cache-Control': 'private, no-store'})


@router.post('/jobs/merge')
async def merge_completed_jobs(
    db: DbDep, filestore: FileStoreDep, redis: RedisDep, user: CurrentUserDep,
    job_ids: Annotated[str, Form()],
    versions: Annotated[str, Form()] = '',
    title: Annotated[str, Form()] = 'Trascrizione unita',
):
    ids = _ids(job_ids)
    jobs = _sources(db, filestore, user.id, ids)
    kinds = [x.strip() for x in versions.split(',')] if versions else ['original'] * len(ids)
    if len(kinds) != len(ids) or any(x not in ALLOWED_VERSIONS for x in kinds):
        raise HTTPException(status_code=422, detail='Versioni selezionate non valide')
    for old, kind in zip(jobs, kinds, strict=True):
        if _source_result(filestore, old.id, kind) is None:
            raise HTTPException(status_code=422, detail='Una versione non è più disponibile')
    title = title.strip()
    if not title or len(title) > 100 or re.search(r'[\\/\r\n\x00-\x1f]', title):
        raise HTTPException(status_code=422, detail='Nome unione non valido')
    display_name = Path(title).stem[:100].strip(' .') + '.wav'
    job = Job(
        filename=display_name,
        status=JobStatus.QUEUED,
        owner_id=user.id,
        language=jobs[0].language,
        enable_diarization=False,
        llm_correction_enabled=False,
        summary_enabled=False,
        source_job_id=None,
    )
    directory = filestore.prepare_upload_dir(job.id)
    job.filepath = str(directory / 'registrazione-unita.wav')
    manifest = directory / SOURCES_MANIFEST
    temp = directory / f'.{SOURCES_MANIFEST}.tmp'
    temp.write_text(json.dumps({
        'version': 1,
        'sources': [{'job_id': job_id, 'transcript': version}
                    for job_id, version in zip(ids, kinds, strict=True)],
    }, ensure_ascii=False), encoding='utf-8')
    temp.replace(manifest)
    db.insert_job(job)
    try:
        enqueue_join(redis, job.id)
    except Exception as exc:
        job.status = JobStatus.FAILED
        job.error = 'Impossibile avviare la coda di unione'
        db.update_job(job)
        raise HTTPException(status_code=503, detail=job.error) from exc
    return JSONResponse({'job_id': job.id, 'status': 'queued', 'redirect': '/jobs'}, status_code=202)


def enqueue_join(redis, job_id: str):
    return Queue(WORKER_QUEUE_IO, connection=redis).enqueue(
        join_completed_jobs, job_id, job_timeout=14400,
        result_ttl=86400, failure_ttl=86400,
    )
