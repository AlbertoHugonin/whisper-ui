from __future__ import annotations

import pytest
from pydantic import ValidationError

from whisper_ui.core.config import Settings


def _make_settings(tmp_path, **overrides):
    base = {
        "_env_file": "/dev/null",
        "database_path": tmp_path / "test.db",
        "upload_dir": tmp_path / "uploads",
        "output_dir": tmp_path / "outputs",
    }
    base.update(overrides)
    return Settings(**base)


def test_timeout_defaults_are_consistent(tmp_path):
    s = Settings(
        _env_file="/dev/null",
        database_path=tmp_path / "test.db",
        upload_dir=tmp_path / "uploads",
        output_dir=tmp_path / "outputs",
    )
    assert s.job_timeout_floor < s.job_timeout_default <= s.job_timeout_max
    assert s.job_timeout_audio_multiplier >= 1.0
    assert s.stale_job_timeout == s.job_timeout_max + s.stale_job_buffer
    assert s.redis_processing_expiry >= s.stale_job_timeout - s.stale_job_buffer
    assert s.diarize_heartbeat_interval > 0


class TestTimeoutInvariantValidator:
    """Settings must reject inconsistent queue-timeout combinations at
    startup so operators get immediate feedback instead of silently
    counter-intuitive clamping behavior (see PR #34 Finding 3).
    """

    def test_rejects_floor_above_default(self, tmp_path):
        with pytest.raises(ValidationError, match="job_timeout_default"):
            _make_settings(tmp_path, job_timeout_floor=5000, job_timeout_default=3000)

    def test_rejects_redis_expiry_below_stale_timeout(self, tmp_path):
        with pytest.raises(ValidationError, match="redis_processing_expiry"):
            _make_settings(
                tmp_path,
                job_timeout_max=10000,
                stale_job_buffer=1000,
                redis_processing_expiry=5000,  # below 10000 + 1000 = 11000
            )


@pytest.mark.parametrize(
    "bad_url",
    [
        "http://ollama:bad",  # InvalidURL: bad port
        "ftp://host:11434",  # wrong scheme
        "not-a-url",  # httpx.URL parses but host is empty
        "http://",  # empty host
    ],
)
def test_ollama_base_url_rejected_when_invalid(tmp_path, bad_url):
    """Malformed URLs must fail at service startup rather than silently
    crash every opted-in job inside HttpxOllamaClient.__init__."""
    with pytest.raises(ValidationError):
        _make_settings(tmp_path, ollama_base_url=bad_url)


def test_remote_worker_can_advertise_diarization_without_frontend_token(tmp_path):
    settings = _make_settings(tmp_path, hf_token="", diarization_remote_worker_available=True)
    assert settings.diarization_available is True
    assert settings.diarization_default_for_form is True
    settings = _make_settings(tmp_path, hf_token="", diarization_remote_worker_available=False)
    assert settings.diarization_available is False
