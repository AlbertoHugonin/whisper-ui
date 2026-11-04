"""Audio merge manifest safety and exact FFmpeg ordering."""
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from whisper_ui.pipeline.merge_audio import MANIFEST_NAME, AudioMergeStage


def _make_parts(tmp_path, filenames=('part-0001.mp3', 'part-0002.wav')):
    for name in filenames:
        (tmp_path / name).write_bytes(b'fake media')
    manifest = tmp_path / MANIFEST_NAME
    manifest.write_text(json.dumps({
        'version': 1,
        'parts': [{'filename': name, 'original_name': f'original_{i}'} for i, name in enumerate(filenames)],
    }), encoding='utf-8')
    return {'input_path': str(tmp_path / 'registrazione-unita.wav'), 'merge_manifest_path': str(manifest)}


def test_merge_ffmpeg_uses_manifest_order_and_atomic_output(tmp_path):
    ctx = _make_parts(tmp_path, ('part-0002.wav', 'part-0001.mp3'))
    seen = []

    def fake_ffmpeg(argv, **kwargs):
        seen.append(argv)
        Path(argv[-1]).write_bytes(b'RIFF' + b'\x00' * 90)
        return subprocess.CompletedProcess(argv, 0, b'', b'')

    progress = []
    with patch('whisper_ui.pipeline.merge_audio.subprocess.run', side_effect=fake_ffmpeg):
        result = AudioMergeStage().execute(ctx, lambda pct, message: progress.append(pct))
    assert result['input_path'].endswith('registrazione-unita.wav')
    assert Path(result['input_path']).is_file()
    cmd = seen[0]
    assert cmd[cmd.index('-i') + 1].endswith('part-0002.wav')
    assert cmd[cmd.index('-i', cmd.index('-i') + 1) + 1].endswith('part-0001.mp3')
    assert 'concat=n=2:v=0:a=1[combined]' in cmd[cmd.index('-filter_complex') + 1]
    assert '-c:a' in cmd and cmd[cmd.index('-c:a') + 1] == 'pcm_s16le'
    assert '-ar' in cmd and cmd[cmd.index('-ar') + 1] == '16000'
    assert progress == [.05, 1.0]
    assert not list(tmp_path.glob('.*.wav'))


@pytest.mark.parametrize('malformed', [
    {'version': 1, 'parts': [{'filename': '../bad.wav'}, {'filename': 'part-0002.wav'}]},
    {'version': 1, 'parts': [{'filename': 'part-0001.mp3'}, {'filename': 'part-0001.mp3'}]},
    {'version': 1, 'parts': [{'filename': 'part-0001.mp3'}]},
    {'version': 2, 'parts': [{'filename': 'part-0001.mp3'}, {'filename': 'part-0002.wav'}]},
])
def test_rejects_bad_manifest_before_ffmpeg(tmp_path, malformed):
    ctx = _make_parts(tmp_path)
    Path(ctx['merge_manifest_path']).write_text(json.dumps(malformed))
    with patch('whisper_ui.pipeline.merge_audio.subprocess.run') as ffmpeg, pytest.raises(ValueError):
        AudioMergeStage().execute(ctx)
    ffmpeg.assert_not_called()


def test_missing_audio_part_is_rejected(tmp_path):
    ctx = _make_parts(tmp_path)
    (tmp_path / 'part-0002.wav').unlink()
    with pytest.raises(ValueError, match='missing'):
        AudioMergeStage().execute(ctx)


def test_ffmpeg_failure_never_publishes_partial_merged_audio(tmp_path):
    ctx = _make_parts(tmp_path)

    def failed(argv, **kwargs):
        Path(argv[-1]).write_bytes(b'partially written')
        return subprocess.CompletedProcess(argv, 1, b'', b'no audio stream')

    with patch('whisper_ui.pipeline.merge_audio.subprocess.run', side_effect=failed), pytest.raises(ValueError):
        AudioMergeStage().execute(ctx)
    assert not Path(ctx['input_path']).exists()
    assert not list(tmp_path.glob('.*.wav'))
