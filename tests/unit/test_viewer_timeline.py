"""The player, peak API and transcript remain connected after template refactors."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "src/whisper_ui/web"


def test_waveform_api_requires_job_owner():
    source = (ROOT / "routes/viewer.py").read_text()
    start = source.index('@router.get("/viewer/{job_id}/waveform")')
    end = source.index('@router.get("/viewer/{job_id}/processed-audio")')
    fn = source[start:end]
    assert "owner_filter(user)" in fn
    assert "JobStatus.COMPLETED" in fn
    assert "filestore.get_output_artifact" in fn
    assert "Cache-Control" in fn
