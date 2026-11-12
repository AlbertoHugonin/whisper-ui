"""Behavioural tests for ``docker/entrypoint-worker.sh`` argument assembly.

The entrypoint builds the RQ worker invocation from environment variables
(``DEVICE`` selects ``SimpleWorker``; ``WORKER_MAX_IDLE_TIME`` adds a validated
``--max-idle-time``). These tests run the real script with a stub ``python`` on
``PATH`` that prints the argv it was ``exec``'d with, so we assert on the exact
flags the worker would receive without launching RQ or a container.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

_ENTRYPOINT = Path(__file__).resolve().parents[2] / "docker" / "entrypoint-worker.sh"
_STUB_PREFIX = "STUB_PYTHON_ARGS:"


def _run_entrypoint(tmp_path: Path, env_overrides: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run the entrypoint with a stub ``python`` and return the completed process.

    The stub shadows the real interpreter via ``PATH`` and echoes its arguments,
    so ``result.stdout`` carries exactly what ``exec python -m whisper_ui.worker
    worker ...`` would have launched, and ``result.stderr`` carries any WARNING.
    """
    stub = tmp_path / "python"
    stub.write_text(f'#!/bin/sh\necho "{_STUB_PREFIX} $*"\n')
    stub.chmod(0o755)

    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ.get('PATH', '')}", **env_overrides}
    return subprocess.run(
        ["bash", str(_ENTRYPOINT)],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )


def _argv_line(result: subprocess.CompletedProcess[str]) -> str:
    """Extract the rq argv line the stub python printed."""
    for line in result.stdout.splitlines():
        if line.startswith(_STUB_PREFIX):
            return line
    raise AssertionError(f"entrypoint did not exec the stub python; stdout:\n{result.stdout}")


def test_rocm_worker_gets_simpleworker_and_idle_flag(tmp_path: Path) -> None:
    argv = _argv_line(_run_entrypoint(tmp_path, {"DEVICE": "rocm", "WORKER_MAX_IDLE_TIME": "300"}))

    assert "--worker-class rq.SimpleWorker" in argv
    assert "--max-idle-time 300" in argv


def test_cuda_worker_without_idle_time_omits_idle_flag(tmp_path: Path) -> None:
    argv = _argv_line(_run_entrypoint(tmp_path, {"DEVICE": "cuda"}))

    assert "--worker-class rq.SimpleWorker" in argv
    assert "--max-idle-time" not in argv


def test_whitespace_value_does_not_inject_extra_queue(tmp_path: Path) -> None:
    # A value like "300 foo" must not word-split into the command and add a bogus
    # queue ("foo") or a bare "300" token.
    argv = _argv_line(
        _run_entrypoint(tmp_path, {"DEVICE": "rocm", "WORKER_MAX_IDLE_TIME": "300 foo", "WORKER_QUEUES": "whisper:gpu"})
    )

    assert "--max-idle-time" not in argv
    assert "foo" not in argv
    assert argv.rstrip().endswith("whisper:gpu")
