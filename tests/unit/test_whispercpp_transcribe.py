from __future__ import annotations

import contextlib
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from whisper_ui.core.config import Settings
from whisper_ui.core.exceptions import TranscriptionError
from whisper_ui.pipeline.whispercpp_transcribe import WhisperCppTranscribeStage
from whisper_ui.worker.stage_tasks import _build_transcribe_stage

_MODULE = "whisper_ui.pipeline.whispercpp_transcribe"


def _fake_cli_writer(payload: dict, calls: list[list[str]] | None = None):
    """Return a subprocess.run stand-in that writes ``payload`` to <-of>.json.

    When ``calls`` is given, every invoked command line is appended to it so
    tests can assert on the exact flags passed to whisper-cli.
    """

    def _run(cmd, *args, **kwargs):  # resilient to subprocess.run kwargs (encoding/errors/etc.)
        if calls is not None:
            calls.append(list(cmd))
        out_prefix = cmd[cmd.index("-of") + 1]
        Path(f"{out_prefix}.json").write_text(json.dumps(payload), encoding="utf-8")
        return MagicMock(returncode=0, stderr="", stdout="")

    return _run


@contextlib.contextmanager
def _patched_model_paths(vad_path: str = "/m/vad.bin"):
    """Patch both model resolutions so execute() never touches the network."""
    with (
        patch.object(WhisperCppTranscribeStage, "_resolve_model_path", return_value="/m/ggml-large-v3.bin"),
        patch.object(WhisperCppTranscribeStage, "_resolve_vad_model_path", return_value=vad_path),
    ):
        yield


class TestExecute:
    def test_parses_json_and_sets_context_keys(self):
        stage = WhisperCppTranscribeStage(model_name="large-v3")
        payload = {
            "result": {"language": "en"},
            "transcription": [
                {"offsets": {"from": 0, "to": 3000}, "text": " Hello world"},
                {"offsets": {"from": 3000, "to": 6000}, "text": "second"},
            ],
        }
        mock_whisperx = MagicMock()
        mock_whisperx.load_audio.return_value = "AUDIO_ARRAY"
        with (
            _patched_model_paths(),
            patch(f"{_MODULE}.subprocess.run", side_effect=_fake_cli_writer(payload)),
            patch.dict("sys.modules", {"whisperx": mock_whisperx}),
        ):
            ctx = stage.execute({"audio_path": "/tmp/a.16k.wav", "language": "en"})

        assert ctx["transcription_result"]["language"] == "en"
        assert ctx["transcription_result"]["segments"] == [
            {"start": 0.0, "end": 3.0, "text": "Hello world"},
            {"start": 3.0, "end": 6.0, "text": "second"},
        ]
        assert ctx["whisperx_audio"] == "AUDIO_ARRAY"

    def test_nonzero_exit_raises(self):
        stage = WhisperCppTranscribeStage()
        with (
            _patched_model_paths(),
            patch(f"{_MODULE}.subprocess.run", return_value=MagicMock(returncode=2, stderr="boom")),
            pytest.raises(TranscriptionError, match="exit 2"),
        ):
            stage.execute({"audio_path": "/tmp/a.wav"})


class TestCliFlags:
    _PAYLOAD = {"result": {"language": "zh"}, "transcription": []}

    def _run_and_capture(self, stage: WhisperCppTranscribeStage) -> list[str]:
        calls: list[list[str]] = []
        mock_whisperx = MagicMock()
        with (
            _patched_model_paths(),
            patch(f"{_MODULE}.subprocess.run", side_effect=_fake_cli_writer(self._PAYLOAD, calls)),
            patch.dict("sys.modules", {"whisperx": mock_whisperx}),
        ):
            stage.execute({"audio_path": "/tmp/a.wav", "language": "zh"})
        return calls[0]

    def test_default_flags_enable_vad_and_disable_context(self):
        cmd = self._run_and_capture(WhisperCppTranscribeStage())
        assert "--vad" in cmd
        assert cmd[cmd.index("-vm") + 1] == "/m/vad.bin"
        assert cmd[cmd.index("-mc") + 1] == "0"
        # Conservative recall preset: protect weak words without removing VAD.
        assert cmd[cmd.index("-vt") + 1] == "0.25"
        assert cmd[cmd.index("-vspd") + 1] == "100"
        assert cmd[cmd.index("-vsd") + 1] == "300"
        assert cmd[cmd.index("-vp") + 1] == "400"
        assert cmd[cmd.index("-vo") + 1] == "0.2"

    def test_vad_disabled_omits_vad_flags(self):
        cmd = self._run_and_capture(WhisperCppTranscribeStage(vad=False))
        assert "--vad" not in cmd
        assert "-vm" not in cmd
        for option in ("-vt", "-vspd", "-vsd", "-vp", "-vo"):
            assert option not in cmd

    def test_vad_tuning_is_passed_to_whisper_cli(self):
        cmd = self._run_and_capture(
            WhisperCppTranscribeStage(
                vad_threshold=0.10,
                vad_min_speech_ms=80,
                vad_min_silence_ms=450,
                vad_speech_pad_ms=600,
                vad_samples_overlap=0.35,
            )
        )
        for flag, value in (("-vt", "0.1"), ("-vspd", "80"), ("-vsd", "450"), ("-vp", "600"), ("-vo", "0.35")):
            assert cmd[cmd.index(flag) + 1] == value

    def test_auto_language_passes_through_to_cli(self):
        calls: list[list[str]] = []
        payload = {"result": {"language": "en"}, "transcription": []}
        mock_whisperx = MagicMock()
        stage = WhisperCppTranscribeStage()
        with (
            _patched_model_paths(),
            patch(f"{_MODULE}.subprocess.run", side_effect=_fake_cli_writer(payload, calls)),
            patch.dict("sys.modules", {"whisperx": mock_whisperx}),
        ):
            ctx = stage.execute({"audio_path": "/tmp/a.wav", "language": "auto"})
        cmd = calls[0]
        assert cmd[cmd.index("-l") + 1] == "auto"
        # The detected language reported by whisper.cpp flows into the result.
        assert ctx["transcription_result"]["language"] == "en"


class TestBackendSelection:
    @staticmethod
    def _runtime(backend: str) -> MagicMock:
        runtime = MagicMock()
        runtime.settings.transcribe_backend = backend
        runtime.settings.whispercpp_binary = "whisper-cli"
        runtime.settings.whispercpp_threads = 0
        runtime.settings.whispercpp_vad = True
        runtime.settings.whispercpp_vad_model = "ggml-silero-v5.1.2.bin"
        runtime.settings.whispercpp_vad_threshold = 0.25
        runtime.settings.whispercpp_vad_min_speech_ms = 100
        runtime.settings.whispercpp_vad_min_silence_ms = 300
        runtime.settings.whispercpp_vad_speech_pad_ms = 400
        runtime.settings.whispercpp_vad_samples_overlap = 0.2
        runtime.settings.whispercpp_max_context = 0
        runtime.settings.compute_type = "int8_float16"
        runtime.settings.device = "rocm"
        return runtime

    def test_job_can_disable_vad_independently_of_server_default(self):
        job = MagicMock()
        job.model_name = "large-v3"
        job.vad_threshold = 0.15
        job.whisper_beam_size = 5
        job.vad_enabled = False
        job.vad_min_speech_ms = 100
        job.vad_min_silence_ms = 300
        job.vad_speech_pad_ms = 400
        job.vad_samples_overlap = 0.2
        stage = _build_transcribe_stage(job, self._runtime("whispercpp"), "cuda")
        assert stage._vad is False
        calls = []
        with patch(
            f"{_MODULE}.subprocess.run",
            side_effect=_fake_cli_writer(
                {"result": {"language": "it"}, "transcription": []},
                calls,
            ),
        ):
            stage._run_whisper_cli("/m/model.bin", "/tmp/audio.wav", "it", None)
        for flag in ("--vad", "-vm", "-vt", "-vspd", "-vsd", "-vp", "-vo"):
            assert flag not in calls[0]


class TestTranscribeBackendConfig:
    # _env_file=None isolates the validator test from a developer's local .env.

    def test_rejects_unknown_backend(self):
        with pytest.raises(ValidationError):
            Settings(transcribe_backend="bogus", _env_file=None)
