"""Editor-style viewport: toolbar stays fixed, only transcript scrolls."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / 'src/whisper_ui/web'
VIEWER = (ROOT / 'templates/viewer.html').read_text()
CSS = (ROOT / 'static/css/input.css').read_text()
JS = (ROOT / 'static/viewer-docked-layout.js').read_text()


def test_viewer_has_one_scrollable_transcript_under_fixed_controls():
    assert VIEWER.count('class="viewer-transcript-frame"') == 1
    assert VIEWER.count('class="viewer-dock viewer-controls-fixed') == 1
    assert VIEWER.index('viewer-controls-fixed') < VIEWER.index('viewer-transcript-frame')
    assert 'aria-label="Trascrizione, area a scorrimento indipendente"' in VIEWER
    assert '.viewer-transcript-frame .viewer-transcript-head' in CSS
    assert '.viewer-workspace .viewer-transcript-frame .viewer-transcript-list' in CSS
    assert 'overscroll-behavior:contain' in CSS


def test_viewport_layout_has_minimum_reading_area_and_safe_fallback():
    assert 'viewer-page-fit' in JS and 'viewer-page-fit' in CSS
    assert 'const minTranscript = mobile() ? 200 : 220' in JS
    assert 'page.classList.toggle(\'viewer-page-fit\', fit)' in JS
    assert 'ResizeObserver' in JS
    assert 'window.visualViewport?.addEventListener' in JS
    assert '#page-content:not(.viewer-page-fit)' in CSS
    assert 'height:clamp(300px,55dvh,750px)' in CSS


def test_editor_script_attached_once_with_cleanup():
    assert VIEWER.count("path='viewer-docked-layout.js'") == 1
    assert 'this._editorLayout = window.WhisperDockedLayout?.init();' in VIEWER
    assert 'this._editorLayout?.destroy();' in VIEWER
    assert 'this._timeline?.destroy();' in VIEWER
    assert "this.scrollSegmentWithinTranscript(el)" in VIEWER
    assert "window.WhisperTimeline.init({" in VIEWER
