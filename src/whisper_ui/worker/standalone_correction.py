"""Nonblocking, repeatable correction of an already-completed transcript.

No Whisper model runs here. result.json remains immutable; correction is saved
atomically as result.corrected.json so Studio can switch versions instantly.
"""
from __future__ import annotations

import logging
from typing import Any

from rq import Queue
from rq.job import Job as RQJob

from whisper_ui.core.constants import WORKER_QUEUE_LLM
from whisper_ui.core.models import JobStatus
from whisper_ui.worker.runtime import build_worker_runtime

logger = logging.getLogger(__name__)
_KEY_PREFIX = "whisper:correction:"
_ACTIVE = {"queued", "started", "deferred", "scheduled"}


def correction_status(redis, job_id: str) -> dict[str, str]:
    raw = redis.get(_KEY_PREFIX + job_id)
    if not raw:
        return {"status": "not_requested"}
    task_id = raw.decode() if isinstance(raw, bytes) else str(raw)
    if task_id == "pending":
        return {"status": "queued"}
    try:
        task = RQJob.fetch(task_id, connection=redis)
        state_obj = task.get_status(refresh=True)
        state = getattr(state_obj, "value", str(state_obj))
    except Exception:
        return {"status": "unknown"}
    return {"status": state, "task_id": task_id}


def enqueue_correction(redis, job_id: str) -> dict[str, str]:
    key = _KEY_PREFIX + job_id
    old = correction_status(redis, job_id)
    if old["status"] in _ACTIVE:
        return old
    # Compare-and-delete prevents one submission from invalidating another's
    # lock after RQ reported a previous attempt terminal.
    previous = redis.get(key)
    if previous is not None:
        redis.eval(
            "if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end return 0",
            1, key, previous,
        )
    if not redis.set(key, "pending", nx=True, ex=14400):
        return correction_status(redis, job_id)
    try:
        task = Queue(WORKER_QUEUE_LLM, connection=redis).enqueue(
            run_standalone_correction, job_id, job_timeout=7200,
            result_ttl=86400, failure_ttl=86400,
        )
        redis.set(key, task.id, ex=86400)
        return {"status": "queued", "task_id": task.id}
    except Exception:
        redis.delete(key)
        raise


def run_standalone_correction(job_id: str) -> dict[str, Any]:
    # Avoid the parent pipeline reporter: the transcription is already final.
    with build_worker_runtime(job_id) as runtime:
        job = runtime.db.get_job(job_id)
        if job is None or job.status != JobStatus.COMPLETED:
            raise ValueError("Only completed transcripts can be corrected")
        source = runtime.filestore.load_result(job_id)
        if source is None:
            raise ValueError("Original transcript missing")
        settings = runtime.settings
        if not settings.llm_correction_available:
            raise RuntimeError("LLM correction is not configured")
        from whisper_ui.pipeline.llm_correction import LLMCorrectionStage
        stage = LLMCorrectionStage(
            base_url=settings.ollama_base_url,
            model=settings.ollama_model,
            keep_alive=settings.ollama_keep_alive,
            chunk_size=settings.llm_chunk_size,
            chunk_context=settings.llm_chunk_context,
            temperature=settings.llm_temperature,
            request_timeout=float(settings.ollama_request_timeout),
            think=settings.ollama_think,
            context_prompt=job.llm_context_prompt,
            backend=settings.llm_api_mode,
        )
        context: dict[str, Any] = {"transcript_result": source, "quality_warning": job.quality_warning}
        try:
            stage.execute(context)
        finally:
            stage.cleanup()
        total = context.get("llm_total_chunks", 0)
        failures = context.get("llm_failed_chunks", 0)
        if not total or failures == total:
            raise RuntimeError("LLM correction did not produce any validated chunks")
        # Original remains published even if this save fails or a new attempt
        # replaces the previous corrected version.
        runtime.filestore.save_corrected_result(job_id, source)
        return {"corrected": True, "chunks": total, "failed_chunks": failures}
