"""Remote worker DB adapter; SQLite never crosses machine boundaries."""
from __future__ import annotations

import dataclasses
import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from whisper_ui.core.models import Job, JobStatus


class RemoteJobDatabase:
    def __init__(self, base_url: str, token: str):
        if not (base_url.startswith('http://192.168.0.120:') or base_url.startswith('http://whisper-frontend:')):
            raise ValueError('Worker DB endpoint must be the trusted LAN frontend')
        if len(token) < 32:
            raise ValueError('WHISPER_WORKER_TOKEN is required for remote DB')
        self._base = base_url.rstrip('/') + '/internal/worker'
        self._token = token

    def _request(self, suffix: str, method: str = 'GET', payload: dict | None = None):
        raw = json.dumps(payload).encode() if payload is not None else None
        request = Request(self._base + suffix, data=raw, method=method,
                          headers={'X-Worker-Token': self._token, 'Content-Type': 'application/json'})
        with urlopen(request, timeout=20) as response:
            return json.load(response)

    def get_job(self, job_id: str, *, owner_id: int | None = None):
        try:
            data = self._request('/jobs/' + job_id)
        except HTTPError as exc:
            if exc.code == 404:
                return None
            raise
        data['status'] = JobStatus(data['status'])
        job = Job(**data)
        if owner_id is not None and job.owner_id != owner_id:
            return None
        return job

    def update_job(self, job: Job) -> None:
        job.touch()
        self._request('/jobs/' + job.id, 'PUT', dataclasses.asdict(job))

    def update_job_progress(self, job_id: str, progress: float, message: str) -> None:
        self._request('/jobs/' + job_id + '/progress', 'PUT', {'progress': progress, 'message': message})

    def close(self):
        pass
