"""Prevent regressions in the shared app width and nested transcription scrolling."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "src/whisper_ui/web"
TEMPLATES = ROOT / "templates"
CSS = (ROOT / "static/css/input.css").read_text()


def test_all_main_pages_use_wide_shared_shell():
    base = (TEMPLATES / "base.html").read_text()
    shared = (TEMPLATES / "shared_viewer.html").read_text()
    assert 'id="page-content" class="app-page-shell ' in base
    assert 'shared-page-shell' in shared
    assert 'max-width:1920px' in CSS
    assert 'max-w-4xl' not in base
    assert 'max-w-4xl' not in shared


def test_only_scroll_specific_panels_and_prevent_scroll_chaining():
    assert 'overscroll-behavior:contain' in CSS
    for template, marker in [
        ('viewer.html', 'viewer-transcript-list'),
        ('shared_viewer.html', 'shared-transcript-list'),
        ('jobs.html', 'jobs-scroll-panel'),
        ('admin_jobs.html', 'jobs-scroll-panel'),
        ('admin_users.html', 'admin-table-scroll'),
    ]:
        assert marker in (TEMPLATES / template).read_text()
    # Native scrollIntoView scrolls ancestors (including the whole document).
    for filename in ('viewer.html', 'shared_viewer.html'):
        content = (TEMPLATES / filename).read_text()
        assert 'scrollSegmentWithinTranscript(el)' in content
        assert 'pane.scrollTo({' in content
        assert '.scrollIntoView(' not in content


def test_waveform_owns_wheel_only_when_zoomed():
    js = (ROOT / 'static/viewer-timeline.js').read_text()
    assert 'else if (zoom>1)' in js
    assert 'event.preventDefault();' in js
    assert '?v=clean-waveform1' in (TEMPLATES / 'viewer.html').read_text()
    assert '?v=' in (TEMPLATES / 'base.html').read_text()
