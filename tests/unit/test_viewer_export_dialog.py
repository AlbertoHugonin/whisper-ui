"""Exports live in a single accessible modal, not in the sticky audio dock."""
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace

from jinja2 import Environment, FileSystemLoader

ROOT = Path(__file__).resolve().parents[2] / 'src/whisper_ui/web'
TEMPLATES = ROOT / 'templates'
FORMATS = ('docx', 'json', 'srt', 'txt', 'vtt')


class Nodes(HTMLParser):
    def __init__(self):
        super().__init__()
        self.nodes = []

    def handle_starttag(self, tag, attrs):
        self.nodes.append((tag, dict(attrs)))


def rendered_export_dialog(*, has_segments=True, share_token=None, has_media=True, has_processed=False):
    env = Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=True)
    labels = SimpleNamespace(
        VIEWER_EXPORT_TOOLTIPS={name: f'Download {name}' for name in FORMATS},
        VIEWER_DOWNLOAD_MEDIA='Download Media', VIEWER_COPY='Copy Full Text',
        VIEWER_COPIED='Copied', VIEWER_SHARE='Share',
        VIEWER_SHARE_LINK_COPIED='Link copied', VIEWER_SHARE_COPY_LINK='Copy share link',
        VIEWER_SHARE_REVOKE='Revoke', VIEWER_SHARE_REVOKE_CONFIRM='Confirm revoke?',
    )
    output = env.get_template('_viewer_export_dialog.html').render(
        job=SimpleNamespace(id='0123456789abcdef0123456789abcdef', share_token=share_token),
        result=SimpleNamespace(segments=['segment'] if has_segments else []),
        export_formats=FORMATS, labels=labels, media_available=has_media,
        original_available=has_media, processed_available=has_processed,
        icon_download=lambda *args: '<svg></svg>', icon_copy=lambda *args: '<svg></svg>',
        icon_link=lambda *args: '<svg></svg>',
    )
    parser = Nodes()
    parser.feed(output)
    return output, parser.nodes


def test_one_dialog_and_real_download_links():
    markup, nodes = rendered_export_dialog()
    dialogs = [attrs for tag, attrs in nodes if tag == 'dialog']
    assert len(dialogs) == 1
    assert dialogs[0]['id'] == 'viewer-export-dialog'
    assert dialogs[0]['aria-labelledby'] == 'viewer-export-title'
    urls = [attrs['href'] for tag, attrs in nodes if tag == 'a']
    assert {f'/viewer/0123456789abcdef0123456789abcdef/export/{fmt}' for fmt in FORMATS} <= set(urls)
    assert '/viewer/0123456789abcdef0123456789abcdef/media' in urls
    assert 'PDF' not in markup  # PDF is not implemented by the export factory.
    assert "closest('dialog').close()" in markup


def test_processed_audio_only_has_its_own_download_link():
    markup, nodes = rendered_export_dialog(has_media=False, has_processed=True)
    urls = [attrs.get('href', '') for tag, attrs in nodes if tag == 'a']
    assert '/viewer/0123456789abcdef0123456789abcdef/processed-audio?download=1' in urls
    assert '/viewer/0123456789abcdef0123456789abcdef/media' not in urls
    assert 'Scarica audio elaborato (WAV)' in markup


def test_processed_audio_download_hidden_when_missing():
    _, nodes = rendered_export_dialog(has_media=True, has_processed=False)
    assert not any('/processed-audio?' in attrs.get('href', '') for tag, attrs in nodes)


def test_share_actions_preserved_with_and_without_existing_link():
    _, no_link_nodes = rendered_export_dialog(share_token=None)
    assert any(attrs.get('hx-post', '').endswith('/share') for tag, attrs in no_link_nodes)
    markup, existing_nodes = rendered_export_dialog(share_token='secure-test-token')
    assert any(attrs.get('hx-delete', '').endswith('/share') for tag, attrs in existing_nodes)
    assert '/shared/secure-test-token' in markup


def test_no_segments_does_not_offer_copy_unavailable_media():
    _, nodes = rendered_export_dialog(has_segments=False, has_media=False)
    urls = [attrs.get('href', '') for tag, attrs in nodes if tag == 'a']
    assert not any(link.endswith('/media') for link in urls)
    assert not any('whisperCopy(Array.from(' in attrs.get('@click', '') for tag, attrs in nodes)


def test_export_button_is_in_transcript_header_not_fixed_toolbar():
    source = (TEMPLATES / 'viewer.html').read_text()
    assert source.count('include "_viewer_export_dialog.html"') == 2  # mutually exclusive branches
    assert source.count('aria-haspopup="dialog"') == 2
    assert source.index('viewer-controls-fixed') < source.index('viewer-transcript-head') < source.index('viewer-export-trigger')
    assert 'viewer-export-actions flex flex-wrap' not in source
    assert 'format/{{ fmt }}' not in source
    assert 'Boolean($refs.exportDialog?.open)' in source


def test_the_dialog_is_viewport_bounded_and_scrollable_on_mobile():
    css = (ROOT / 'static/css/input.css').read_text()
    assert '.viewer-export-dialog::backdrop' in css
    assert 'max-height:min(86dvh,760px)' in css
    assert '.viewer-export-scroll{overflow-y:auto;overscroll-behavior:contain' in css
    assert 'grid-template-columns:1fr' in css
