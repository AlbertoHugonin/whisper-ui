"""Concatenate existing transcriptions in their chosen order, without ASR/GPU.

The new job owns its transcript and optional merged audio. Source results are
read only. Duration offsets use the actual processed audio duration when all
source audio exists, and the stored transcript duration otherwise.
"""
from __future__ import annotations

import json
import logging
import os
import shutil

from whisper_ui.core.models import JobStatus, Segment, TranscriptResult
from whisper_ui.pipeline.audio_probe import get_audio_duration_seconds
from whisper_ui.pipeline.merge_audio import MANIFEST_NAME, AudioMergeStage
from whisper_ui.worker.runtime import build_worker_runtime

logger = logging.getLogger(__name__)
SOURCES_MANIFEST = 'joined-sources.json'
ALLOWED_VERSIONS = {'original', 'corrected', 'custom'}
MAX_JOINED_JOBS = 20


def _source_audio(filestore, job):
    return (filestore.get_output_artifact(job.id, 'processed.wav')
            or filestore.get_any_media_path(job.id, job.filepath))


def _source_result(filestore, source_id: str, version: str):
    readers = {
        'original': filestore.load_result,
        'corrected': filestore.load_corrected_result,
        'custom': filestore.load_custom_result,
    }
    if version not in readers:
        raise ValueError('Invalid source transcript version')
    return readers[version](source_id)


def _build_joined_transcript(items: list[tuple[TranscriptResult, float]]) -> TranscriptResult:
    """Rebase timestamps and namespace speaker IDs per original job."""
    segments: list[Segment] = []
    offset = 0.0
    languages = set()
    for part_number, (result, seconds) in enumerate(items, 1):
        if not 0 < seconds < 24 * 3600:
            raise ValueError('Invalid source audio duration')
        languages.add(result.language)
        for part in result.segments:
            if not 0 <= part.start <= part.end <= seconds + 2:
                raise ValueError('Invalid source segment timestamps')
            # WhisperX sometimes places a final word slightly past the
            # physical end of a recording. Clamp *both* boundaries to avoid
            # constructing an end time earlier than the start time.
            segments.append(Segment(
                start=round(offset + min(part.start, seconds), 3),
                end=round(offset + min(part.end, seconds), 3),
                text=part.text,
                speaker=f'File {part_number} · {part.speaker}' if part.speaker else None,
            ))
        offset += seconds
    return TranscriptResult(segments=segments,
                            language=languages.pop() if len(languages) == 1 else 'unknown',
                            duration=round(offset, 3))


def join_completed_jobs(job_id: str) -> dict[str, object]:
    """RQ I/O worker entrypoint; consumes a fixed, owner-validated manifest."""
    with build_worker_runtime(job_id) as runtime:
        job = runtime.db.get_job(job_id)
        if job is None or job.status not in (JobStatus.QUEUED, JobStatus.PROCESSING):
            raise ValueError('Joined job unavailable or already finalized')
        directory = runtime.filestore.prepare_upload_dir(job_id)
        manifest_file = directory / SOURCES_MANIFEST
        try:
            data = json.loads(manifest_file.read_text(encoding='utf-8'))
            sources = data['sources']
            if (data.get('version') != 1 or not isinstance(sources, list)
                    or not 2 <= len(sources) <= MAX_JOINED_JOBS):
                raise ValueError('Invalid joined source list')
            job.status = JobStatus.PROCESSING
            job.progress = .02
            job.progress_message = 'Lettura delle trascrizioni da unire'
            runtime.db.update_job(job)
            parts = []
            media = []
            for source in sources:
                source_id = source.get('job_id')
                version = source.get('transcript', 'original')
                if not isinstance(source_id, str) or len(source_id) != 32 or version not in ALLOWED_VERSIONS:
                    raise ValueError('Invalid joined source reference')
                old = runtime.db.get_job(source_id)
                if (old is None or old.status != JobStatus.COMPLETED
                        or old.owner_id != job.owner_id or old.id == job.id):
                    raise ValueError('A source transcript is no longer accessible')
                result = _source_result(runtime.filestore, source_id, version)
                if result is None:
                    raise ValueError(f'Source transcript is unavailable: {source_id}')
                source_file = _source_audio(runtime.filestore, old)
                duration = (get_audio_duration_seconds(source_file, job_id=source_id)
                            if source_file else None)
                duration = float(duration or old.duration or result.duration or 0)
                duration = max(duration, max((s.end for s in result.segments), default=0))
                parts.append((result, duration))
                media.append(source_file)
            joined = _build_joined_transcript(parts)
            # Optional WAV creation: if retention reclaimed even one audio,
            # preserve the combined *text* rather than inventing silent audio.
            merged_audio = None
            if all(media):
                job.progress_message = "Unione dei file audio nell'ordine selezionato"
                job.progress = .4
                runtime.db.update_job(job)
                part_manifest = {'version': 1, 'parts': []}
                for number, source_file in enumerate(media, 1):
                    filename = f'part-{number:04d}{source_file.suffix.lower()}'
                    source_path = directory / filename
                    # Local worker task: all IO is offloaded from the frontend.
                    shutil.copy2(source_file, source_path)
                    part_manifest['parts'].append({'filename': filename})
                merge_manifest = directory / MANIFEST_NAME
                merge_manifest.write_text(json.dumps(part_manifest), encoding='utf-8')
                merged_audio = directory / 'registrazione-unita.wav'
                AudioMergeStage().execute({
                    'input_path': str(merged_audio),
                    'merge_manifest_path': str(merge_manifest),
                })
                actual_seconds = get_audio_duration_seconds(merged_audio, job_id=job_id)
                if actual_seconds and abs(actual_seconds - joined.duration) > 1.5:
                    logger.warning('Merged audio duration differs from concatenated metadata: %s', job_id)
                output_audio = runtime.filestore._output_dir / job_id / 'processed.wav'
                output_audio.parent.mkdir(parents=True, exist_ok=True)
                try:
                    os.link(merged_audio, output_audio)
                except FileExistsError:
                    output_audio.unlink()
                    os.link(merged_audio, output_audio)
                except OSError:
                    shutil.copy2(merged_audio, output_audio)
                for part in part_manifest['parts']:
                    (directory / part['filename']).unlink(missing_ok=True)
                merge_manifest.unlink(missing_ok=True)
                job.filepath = str(merged_audio)
            result_path = runtime.filestore.save_result(job_id, joined)
            job.result_path = str(result_path)
            job.duration = joined.duration
            job.progress = 1.0
            job.status = JobStatus.COMPLETED
            job.progress_message = ('Trascrizioni e audio uniti' if merged_audio
                                    else 'Trascrizioni unite; audio sorgente non disponibile')
            runtime.db.update_job(job)
            return {'job_id': job_id, 'duration': joined.duration,
                    'segments': len(joined.segments), 'audio_available': bool(merged_audio)}
        except Exception as exc:
            logger.exception('Failed to join completed jobs: %s', job_id)
            job.status = JobStatus.FAILED
            job.error = str(exc)[:500]
            job.progress_message = 'Unione non riuscita'
            runtime.db.update_job(job)
            raise
