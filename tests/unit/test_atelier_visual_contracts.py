"""CSS/DOM contracts established from real Chromium desktop/mobile preview runs."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / 'src/whisper_ui/web'
CSS = (ROOT / 'static/atelier-v2.css').read_text()
T = ROOT / 'templates'


def test_overflow_prevention_in_grid_flex_and_breadcrumbs():
    assert '.atelier-recent-list{grid-template-columns:minmax(0,1fr);min-width:0}' in CSS
    assert '.at2-editor-stack{grid-template-columns:minmax(0,1fr);max-width:100%}' in CSS
    assert '.at2-editor-stack .grid{grid-template-columns:minmax(0,1fr)!important}' in CSS
    assert '.at2-viewer-breadcrumb ul' in CSS
    assert 'word-break:break-word' in CSS


def test_standalone_security_controls_preserved():
    for name in ('login.html', 'register.html'):
        source = (T / name).read_text()
        assert 'action="/login"' in source or 'action="/register"' in source
        assert 'autocomplete=' in source
        assert 'atelier-v2.css' in source
    assert 'at2-shared-reader' in (T / 'shared_viewer.html').read_text()
    assert 'sharedViewerData(' in (T / 'shared_viewer.html').read_text()


def test_new_design_spans_library_and_upload_pipeline():
    s=(T/'upload.html').read_text()
    for token in ('at2-upload-source','at2-upload-presets','at2-upload-basic','at2-upload-audio','at2-upload-ai','at2-upload-submit'):
        assert token in s,token
    s=(T/'jobs.html').read_text()
    for token in ('at2-workspace','_job_list.html','_job_interactions.html'):
        assert token in s,token
