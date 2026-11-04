"""Non-functional invariants for the isolated Atelier frontend and its UI shell."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
T = ROOT / 'src/whisper_ui/web/templates'
CSS = (ROOT / 'src/whisper_ui/web/static/atelier.css').read_text()


def test_new_shell_and_navigation_contract():
    base = (T / 'base.html').read_text()
    assert 'class="atelier-app"' in base
    assert 'class="atelier-mobile-nav"' in base
    assert 'id="page-content" class="app-page-shell atelier-page"' in base
    assert 'atelier.css' in base
    for href in ('/', '/upload', '/jobs', '/presets', '/admin/users', '/admin/jobs'):
        assert f'href="{href}"' in base
    assert '{% block scripts %}' in base
    assert 'id="page-loader"' in base
    assert 'showToast' in base


def test_phone_safe_areas_and_accessibility():
    assert 'viewport-fit=cover' in (T / 'base.html').read_text()
    assert 'env(safe-area-inset-bottom)' in CSS
    assert '100dvh' in CSS
    assert 'font-size:16px!important' in CSS
    assert 'prefers-reduced-motion:reduce' in CSS
    assert ':focus-visible' in CSS
    assert '@media(max-width:700px)' in CSS
    assert '@media(max-width:390px)' in CSS
    assert 'body.atelier-body:has(#page-content.viewer-page-fit){height:auto!important' in CSS


def test_preserved_fragments_and_secondary_maintenance_gate():
    assert '{% include "_dashboard_active.html" %}' in (T/'dashboard.html').read_text()
    for name in ('login.html','register.html','shared_viewer.html'):
        assert 'atelier.css' in (T/name).read_text()
    app=(ROOT/'src/whisper_ui/web/app.py').read_text()
    assert 'WHISPER_SECONDARY_FRONTEND' in app
