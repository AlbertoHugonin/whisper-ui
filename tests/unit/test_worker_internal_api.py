"""Distributed worker DB bridge: token authentication and SQLite ownership."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from whisper_ui.core.models import Job, JobStatus
from whisper_ui.web.routes.worker_internal import router

TOKEN = "d" * 64
JOB_ID = "a" * 32


class Database:
    def __init__(self):
        self.job = Job(id=JOB_ID, filename="demo.wav", owner_id=7, status=JobStatus.QUEUED)
        self.progress = None

    def get_job(self, id):
        return self.job if id == JOB_ID else None

    def update_job(self, job):
        self.job = job

    def update_job_progress(self, id, value, message):
        self.progress = (id, value, message)

    def count_jobs(self, *, status):
        return int(self.job.status == status)


def test_worker_api_requires_token_and_preserves_owner(monkeypatch):
    monkeypatch.setenv("WHISPER_WORKER_TOKEN", TOKEN)
    app = FastAPI()
    db = Database()
    app.state.db = db
    app.include_router(router)
    client = TestClient(app)
    route = f"/internal/worker/jobs/{JOB_ID}"
    assert client.get(route).status_code == 403
    assert client.get(route, headers={"X-Worker-Token": "wrong"}).status_code == 403
    headers = {"X-Worker-Token": TOKEN}
    result = client.get(route, headers=headers)
    assert result.status_code == 200
    assert result.json()["owner_id"] == 7
    assert result.json()["status"] == "queued"
    assert client.get("/internal/worker/pending", headers=headers).json() == {"pending": 0, "queued": 1, "processing": 0}
    update = result.json()
    update["status"] = "processing"
    update["progress"] = 12.5
    assert client.put(route, json=update, headers=headers).status_code == 200
    assert db.job.status == JobStatus.PROCESSING
    assert db.job.progress == 12.5
    assert client.get("/internal/worker/pending", headers=headers).json() == {"pending": 0, "queued": 0, "processing": 1}
    update["owner_id"] = 2
    assert client.put(route, json=update, headers=headers).status_code == 409
    assert db.job.owner_id == 7
    assert client.put(route + "/progress", json={"progress": 30, "message": "audio"}, headers=headers).status_code == 200
    assert db.progress == (JOB_ID, 30, "audio")
    assert client.put(route + "/progress", json={"progress": -100, "message": "bad"}, headers=headers).status_code == 400
    assert client.get("/internal/worker/jobs/traversal", headers=headers).status_code == 400
    monkeypatch.delenv("WHISPER_WORKER_TOKEN")
    assert client.get(route, headers=headers).status_code == 403


def test_remote_worker_adapter_roundtrip(monkeypatch):
    from whisper_ui.storage.remote_job_database import RemoteJobDatabase
    adapter = RemoteJobDatabase('http://192.168.0.120:8085', TOKEN)
    original = Job(id=JOB_ID, filename='source.wav', owner_id=11, status=JobStatus.PROCESSING)
    calls = []
    def fake_request(url, method='GET', payload=None):
        calls.append((url, method, payload))
        if method == 'GET':
            return dataclasses.asdict(original)
        return {'ok': True}
    import dataclasses
    monkeypatch.setattr(adapter, '_request', fake_request)
    job = adapter.get_job(JOB_ID)
    assert job.status == JobStatus.PROCESSING
    adapter.update_job(job)
    assert calls[-1][1] == 'PUT'
    assert calls[-1][2]['id'] == JOB_ID
    adapter.update_job_progress(JOB_ID, 45, 'done')
    assert calls[-1][2] == {'progress':45,'message':'done'}
    with pytest.raises(ValueError):
        RemoteJobDatabase('http://arbitrary-host:8000', TOKEN)
