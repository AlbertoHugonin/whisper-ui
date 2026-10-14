"""Authenticated LAN-only worker data-plane API.

SQLite stays on the frontend host. Remote ROCm workers retrieve and update
jobs through this narrow API instead of opening a SQLite file over NFS.
The bearer token is mandatory and never passed through browser cookies.
"""
from __future__ import annotations

import dataclasses
import os
import re
import secrets

from fastapi import APIRouter, HTTPException, Request
from whisper_ui.core.models import Job, JobStatus

router = APIRouter(prefix="/internal/worker", include_in_schema=False)
_JOB_ID = re.compile(r"^[0-9a-f]{32}$")


def _authorize(request: Request) -> None:
    expected = os.environ.get("WHISPER_WORKER_TOKEN", "")
    supplied = request.headers.get("X-Worker-Token", "")
    if len(expected) < 32 or not secrets.compare_digest(expected, supplied):
        raise HTTPException(status_code=403, detail="Forbidden")


def _job_id(job_id: str) -> None:
    if not _JOB_ID.fullmatch(job_id):
        raise HTTPException(status_code=400, detail="Invalid job id")


@router.get("/jobs/{job_id}")
async def read_job(request: Request, job_id: str):
    _authorize(request)
    _job_id(job_id)
    job = request.app.state.db.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return dataclasses.asdict(job)


@router.put("/jobs/{job_id}")
async def write_job(request: Request, job_id: str):
    _authorize(request)
    _job_id(job_id)
    payload = await request.json()
    if not isinstance(payload, dict) or payload.get("id") != job_id:
        raise HTTPException(status_code=400, detail="Invalid job payload")
    fields = {field.name for field in dataclasses.fields(Job)}
    if set(payload) != fields:
        raise HTTPException(status_code=400, detail="Unexpected job fields")
    try:
        payload["status"] = JobStatus(payload["status"])
        job = Job(**payload)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Invalid job") from exc
    old = request.app.state.db.get_job(job_id)
    if old is None:
        raise HTTPException(status_code=404, detail="Job not found")
    # A stage may update progress, results, and error, but not ownership.
    if old.owner_id != job.owner_id or old.created_at != job.created_at or old.id != job.id:
        raise HTTPException(status_code=409, detail="Immutable job fields")
    request.app.state.db.update_job(job)
    return {"ok": True}


@router.put("/jobs/{job_id}/progress")
async def write_progress(request: Request, job_id: str):
    _authorize(request)
    _job_id(job_id)
    payload = await request.json()
    if not isinstance(payload, dict) or set(payload) != {"progress", "message"}:
        raise HTTPException(status_code=400, detail="Invalid progress payload")
    if (not isinstance(payload["progress"], (int, float))
            or isinstance(payload["progress"], bool)
            or not 0 <= payload["progress"] <= 100
            or not isinstance(payload["message"], str)
            or len(payload["message"]) > 4000):
        raise HTTPException(status_code=400, detail="Invalid progress")
    request.app.state.db.update_job_progress(job_id, float(payload["progress"]), payload["message"])
    return {"ok": True}


@router.get("/pending")
async def pending_jobs(request: Request):
    _authorize(request)
    db = request.app.state.db
    return {"pending": db.count_jobs(status=JobStatus.PENDING.value),
            "queued": db.count_jobs(status=JobStatus.QUEUED.value),
            "processing": db.count_jobs(status=JobStatus.PROCESSING.value)}
