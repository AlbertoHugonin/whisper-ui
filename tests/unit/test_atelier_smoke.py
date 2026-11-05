"""Fast Atelier invariants. Browser click QA is run separately, when needed."""
from pathlib import Path
from jinja2 import Environment

ROOT = Path(__file__).resolve().parents[2] / 'src/whisper_ui/web'
T = ROOT / 'templates'
SCREENS = (
    'dashboard', 'upload', 'jobs', 'viewer', 'studio', 'presets',
    'preset_editor', 'admin_users', 'admin_jobs', 'login',
    'register', 'shared_viewer',
)


def test_template_syntax_and_design_assets():
    env = Environment()
    for name in SCREENS:
        env.parse((T / f'{name}.html').read_text())
    for name in ('base', 'login', 'register', 'shared_viewer'):
        assert 'atelier-v2.css' in (T / f'{name}.html').read_text()


def test_upload_has_one_form_and_explicit_picker_targets():
    html = (T / 'upload.html').read_text()
    for token in ('x-data="uploadForm()"', 'hx-target="#upload-feedback"',
                  'name="merge_mode"', 'name="model_name"',
                  'label for="upload-files"', 'label for="upload-folder"'):
        assert token in html
    assert html.count('id="upload-feedback"') == 1
    assert html.count('x-if="uploading"') == 1
    assert html.count('at2-upload-presets') == 1
    assert html.count('at2-upload-source') == 1
    assert html.count('type="file"') == 2
    css = (ROOT / 'static/atelier-v2.css').read_text()
    assert '.at2-upload-submit{position:relative;bottom:auto;z-index:auto' in css
    assert '.at2-upload-source input[type="file"]' in css


def test_key_actions_survive():
    for name, controls in {
        'jobs': ('_job_list.html', '_job_interactions.html'),
        'viewer': ('viewerData(', '_viewer_export_dialog.html'),
        'studio': ('reviewStudio()', 'saveCustom()'),
        'presets': ('/presets/new', '/presets/{{ item.id }}/edit'),
        'admin_users': ('resetPasswordDialog()', '/admin/users'),
    }.items():
        html = (T / f'{name}.html').read_text()
        for control in controls:
            assert control in html, (name, control)
    assert 'WHISPER_SECONDARY_FRONTEND' in (ROOT / 'app.py').read_text()


def test_spa_router_and_desktop_phone_layout_contract():
    router = (ROOT / 'static/atelier-router.js').read_text()
    css = (ROOT / 'static/atelier-workspaces.css').read_text()
    base = (T / 'base.html').read_text()
    assert 'htmx.ajax(' in router
    assert 'history.pushState(' in router
    assert "window.addEventListener('popstate'" in router
    assert "pageRoute(url.pathname)" in router
    assert "anchor.hasAttribute('download')" in router
    assert "atelier:leaving" in router
    assert 'htmx.config.inlineScriptNonce' in base
    assert 'atelier-router.js' in base and 'atelier-workspaces.css' in base
    assert '@media(min-width:1280px)' in css
    assert '(min-aspect-ratio:17/10)' in css
    assert '(max-aspect-ratio:17/10)' in css
    assert '@media(max-width:700px)' in css
    assert '.at2-upload-side,.at2-editor-sidebar{display:none' in css
    assert 'var UPLOAD_PRESETS' in (T/'upload.html').read_text()
    assert 'var RT_HF_AVAILABLE' in (T/'jobs.html').read_text()
