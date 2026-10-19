"""The player, peak API and transcript remain connected after template refactors."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / 'src/whisper_ui/web'


def test_viewer_references_local_secure_waveform_and_sticky_controls():
    markup = (ROOT / 'templates/viewer.html').read_text()
    assert markup.count('id="viewer-waveform"') == 1
    assert markup.count('id="viewer-seek"') == 1
    assert markup.count('id="media-player"') == 1  # one player, one track at a time
    for item in ('viewer-dock', 'viewer-transcript-list', 'followPlayback', 'WhisperTimeline.init', 'viewer-overview', 'switchAudio(source)', 'timelineZoom(', 'initial_media_source', 'waveform?source='):
        assert item in markup
    assert "path='viewer-timeline.js') }}?v=clean-waveform1" in markup


def test_waveform_api_requires_job_owner():
    source = (ROOT / 'routes/viewer.py').read_text()
    start = source.index('@router.get("/viewer/{job_id}/waveform")')
    end = source.index('@router.get("/viewer/{job_id}/processed-audio")')
    fn = source[start:end]
    assert 'owner_filter(user)' in fn
    assert 'JobStatus.COMPLETED' in fn
    assert 'filestore.get_output_artifact' in fn
    assert 'Cache-Control' in fn


def test_dense_orange_segment_boundaries_are_not_drawn():
    js = (ROOT / 'static/viewer-timeline.js').read_text()
    assert 'for (const seg of segments)' not in js
    assert 'rgba(242,185,91,.36)' not in js
    # One movable playhead still exists to show the playback position.
    assert "const playhead =" in js
    assert "ctx.fillStyle = '#ffbf67'" in js
    # No changes to audio source swapping, zoom, seek or waveform rendering.
    for feature in ('function changeZoom', 'function pan(', 'function scrub(', 'function drawPeaks(', 'function setSource('):
        assert feature in js
