"""Atelier v2 UI invariants without relying on production data or a browser.

These protect the complete screen rewrite's endpoint / form / Alpine contracts.
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2] / 'src/whisper_ui/web'
T = ROOT / 'templates'
CSS = (ROOT / 'static/atelier-v2.css').read_text()

SCREENS = (
    'dashboard.html', 'upload.html', 'jobs.html', 'viewer.html',
    'studio.html', 'presets.html', 'preset_editor.html',
    'admin_users.html', 'admin_jobs.html', 'login.html',
    'register.html', 'shared_viewer.html',
)

def test_every_screen_has_atelier_presentation():
    for page in SCREENS:
        html = (T / page).read_text()
        assert 'atelier' in html.lower() or 'at2-' in html, page
        assert '<h1' in html or 'heading(' in html, page
    for standalone in ('login.html', 'register.html', 'shared_viewer.html'):
        assert 'atelier-v2.css' in (T / standalone).read_text()
    assert 'atelier-v2.css' in (T / 'base.html').read_text()


def test_core_actions_retained():
    upload = (T / 'upload.html').read_text()
    assert 'x-data="uploadForm()"' in upload
    for action in ('/upload', '/upload/url', '/jobs/{{ rt.id }}/re-transcribe'):
        assert action in upload
    for field in ('files','url','merge_mode','language','model_name','vad_threshold',
                  'whisper_keywords','llm_context_prompt','summary_prompt'):
        assert f'name="{field}"' in upload
    for ref in ('fileInput','folderInput'):
        assert f'x-ref="{ref}"' in upload
    assert 'updateFileList' in upload and 'moveFile(' in upload
    assert 'at2-upload-submit' in upload
    jobs = (T / 'jobs.html').read_text()
    for x in ('_job_list.html','_job_interactions.html','mergeCompletedJobsDialog()',
              'retranscribeDialog()','_status_chips.html','_job_search.html'):
        assert x in jobs
    viewer = (T / 'viewer.html').read_text()
    for x in ('viewerData(', 'viewer-seek','viewer-overview','viewer-transcript-frame',
              '_viewer_export_dialog.html','/studio/{{ job.id }}',
              'studio-llm-context'):
        assert x in viewer
    studio = (T / 'studio.html').read_text()
    for x in ('reviewStudio()','studio-timeline-seek','studio-timeline-overview',
              "copyFrom('left')", "copyFrom('right')", 'saveCustom()',
              'generateSummary()'):
        assert x in studio


def test_editor_and_admin_contracts():
    editor=(T / 'preset_editor.html').read_text()
    for name in ('name','description','language','model_name','num_speakers',
                 'vad_enabled','vad_threshold','audio_processing','whisper_keywords',
                 'llm_context_prompt','summary_prompt'):
        assert f'name="{name}"' in editor
    assert 'data-vad-help' in editor
    presets=(T / 'presets.html').read_text()
    for route in ('/presets/new','/presets/{{ item.id }}/edit',
                  '/presets/{{ item.id }}/delete','/upload?preset={{ item.id }}'):
        assert route in presets
    users=(T / 'admin_users.html').read_text()
    for path in ('/admin/users/{{ user.id }}/deactivate',
                 '/admin/users/{{ user.id }}/activate',
                 '/admin/users/{{ user.id }}/toggle-admin',
                 '/admin/users'):
        assert path in users
    assert 'resetPasswordDialog()' in users
    adminjobs=(T / 'admin_jobs.html').read_text()
    assert '_job_list.html' in adminjobs and '_job_interactions.html' in adminjobs


def test_accessible_responsive_patterns():
    for breakpoint in ('@media(max-width:700px)', '@media(max-width:390px)',
                       '@media(max-width:1020px)', 'prefers-reduced-motion'):
        assert breakpoint in CSS
    assert 'safe-area-inset-bottom' in CSS
    assert '100dvh' in CSS
    assert ':focus-visible' in CSS
    for page in ('login.html', 'register.html'):
        source=(T / page).read_text()
        assert 'method="post"' in source
        assert 'autocomplete=' in source
        assert 'showPassword' in source
    shared=(T/'shared_viewer.html').read_text()
    assert 'sharedViewerData(' in shared
    assert 'shared-transcript-list' in shared
    assert 'aria-label="Trascrizione condivisa"' in shared
    assert 'name="new_password"' in (T/'admin_users.html').read_text()


def test_no_double_named_controls_or_duplicate_script_factory():
    for page in ('upload.html','preset_editor.html','jobs.html','viewer.html','studio.html'):
        source=(T/page).read_text()
        assert not re.search(r'<form[^>]*>\s*<form', source)
    assert (T/'viewer.html').read_text().count('class="viewer-transcript-frame"') == 1
    assert (T/'studio.html').read_text().count('x-data="reviewStudio()"') == 1
    assert (T/'upload.html').read_text().count('x-data="uploadForm()"') == 1
