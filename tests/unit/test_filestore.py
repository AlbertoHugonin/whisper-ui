from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.helpers.store import save_upload
from whisper_ui.core.models import Segment, TranscriptResult

if TYPE_CHECKING:
    from whisper_ui.core.config import Settings
    from whisper_ui.storage.filestore import FileStore


def test_save_and_load_result(filestore: FileStore):
    result = TranscriptResult(
        segments=[
            Segment(start=0.0, end=1.0, text="Hello", speaker="SPEAKER_00"),
            Segment(start=1.0, end=2.0, text="World"),
        ],
        language="zh",
        duration=2.0,
    )
    path = filestore.save_result("job1", result)
    assert path.exists()

    loaded = filestore.load_result("job1")
    assert loaded is not None
    assert len(loaded.segments) == 2
    assert loaded.segments[0].text == "Hello"
    assert loaded.segments[0].speaker == "SPEAKER_00"
    assert loaded.segments[1].speaker is None
    assert loaded.language == "zh"
    assert loaded.duration == 2.0


def test_load_result_returns_none_on_truncated_json(filestore: FileStore, settings: Settings):
    """A half-written result.json degrades to "no result" instead of raising."""
    job_dir = settings.output_dir / "corrupt"
    job_dir.mkdir(parents=True)
    (job_dir / "result.json").write_text('{"segments": [{"start": 0.0, "end"', encoding="utf-8")
    assert filestore.load_result("corrupt") is None


def test_save_result_is_atomic_and_leaves_no_temp_file(filestore: FileStore, settings: Settings):
    """save_result renames a temp file into place, leaving no .tmp residue."""
    result = TranscriptResult(segments=[Segment(start=0.0, end=1.0, text="hi")], language="zh", duration=1.0)
    filestore.save_result("atomic", result)
    job_dir = settings.output_dir / "atomic"
    assert (job_dir / "result.json").is_file()
    assert not (job_dir / "result.json.tmp").exists()
    assert filestore.load_result("atomic") is not None


def test_save_upload_sanitizes_path_traversal(filestore: FileStore):
    data = b"malicious content"
    path = save_upload(filestore, "job1", "/etc/passwd", data)
    assert path.parent.name == "job1"
    assert path.name == "passwd"
    assert path.read_bytes() == data


def test_delete_upload_files_removes_uploads_but_keeps_result(filestore: FileStore):
    save_upload(filestore, "job3", "input.mp3", b"data")
    result = TranscriptResult(segments=[], language="zh", duration=0.0)
    filestore.save_result("job3", result)

    removed = filestore.delete_upload_files("job3")
    assert removed is True
    # Upload dir is gone; the saved transcript still loads.
    assert not filestore.get_upload_path("job3", "input.mp3").exists()
    assert filestore.load_result("job3") is not None


def test_copy_source_for_new_job_independent_of_source_deletion(filestore: FileStore):
    save_upload(filestore, "root", "meeting.mp3", b"data")
    filestore.copy_source_for_new_job("root", "meeting.mp3", "version1")

    filestore.delete_job_files("root")

    # Deleting the source job does not affect the copied version.
    assert filestore.get_upload_path("version1", "meeting.mp3").exists()


def test_delete_job_files_raises_on_filesystem_failure(filestore: FileStore, monkeypatch):
    """delete_job_files must surface a filesystem error rather than swallow it,
    so the caller can react. The manual delete routes are row-first (atomic
    terminal-gated row delete, then this best-effort reclaim), so a raise here
    means the row is already gone and the route logs the orphaned files for
    cleanup — see delete_job_files' docstring and the row-first delete routes.
    """
    import shutil

    save_upload(filestore, "job-fail", "input.mp3", b"data")

    def _boom(*_args, **_kwargs):
        raise PermissionError("read-only filesystem")

    monkeypatch.setattr(shutil, "rmtree", _boom)

    with pytest.raises(PermissionError):
        filestore.delete_job_files("job-fail")
