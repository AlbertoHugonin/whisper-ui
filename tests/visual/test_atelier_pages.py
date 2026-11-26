"""Exercise the actual Atelier pages and navigation using an isolated app/database.

Browser requests use a localhost HTTP bridge to TestClient. No production accounts, files,
Redis servers, workers or external inference endpoints are used.
"""

from __future__ import annotations

import base64
import json
import math
import re
import threading
import wave
from array import array
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import fakeredis
import pytest
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

from tests.conftest import TEST_SESSION_SECRET
from whisper_ui.core.config import Settings
from whisper_ui.core.models import Job, JobStatus, Segment, TranscriptResult
from whisper_ui.core.presets import PresetSettings
from whisper_ui.storage import users_repo
from whisper_ui.storage.database import JobDatabase
from whisper_ui.storage.filestore import FileStore
from whisper_ui.storage.presets import PresetRepository
from whisper_ui.web.app import create_app

pytestmark = pytest.mark.visual
ORIGIN = "https://atelier.test"


@pytest.fixture
def atelier_app(tmp_path, monkeypatch):
    settings = Settings(
        database_path=tmp_path / "qa.db",
        upload_dir=tmp_path / "uploads",
        output_dir=tmp_path / "outputs",
        session_secret=TEST_SESSION_SECRET,
        session_https_only=False,
        allow_registration=True,
        device="cpu",
    )
    db = JobDatabase(settings.database_path)
    files = FileStore(settings.upload_dir, settings.output_dir)
    admin = users_repo.create_user(db.conn, "qa-admin", "qa-password-123", is_admin=True)
    users_repo.create_user(db.conn, "qa-reader", "qa-password-123", is_admin=False)
    job = Job(
        id="a" * 32,
        filename="Riunione sintetica per la verifica.wav",
        owner_id=admin.id,
        status=JobStatus.COMPLETED,
        language="it",
        duration=4,
        share_token="atelier-preview-share",
    )
    media = files.get_upload_path(job.id, job.filename)
    media.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(media), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(
            array("h", (int(16000 * math.sin(2 * math.pi * 1000 * i / 16000)) for i in range(64000))).tobytes()
        )
    processed = settings.output_dir / job.id / "processed.wav"
    processed.parent.mkdir(parents=True, exist_ok=True)
    processed.write_bytes(media.read_bytes())
    job.filepath = str(media)
    result = TranscriptResult(
        segments=[Segment(0, 2, "Prima frase di prova.", "S1"), Segment(2, 4, "Seconda frase.", "S2")],
        duration=4,
        language="it",
    )
    job.result_path = str(files.save_result(job.id, result))
    files.save_corrected_result(job.id, result)
    db.insert_job(job)
    preset = PresetRepository(db.conn).create(admin.id, "Preset QA", "Dati sintetici", PresetSettings())
    monkeypatch.setenv("SESSION_HTTPS_ONLY", "false")
    app = create_app()
    app.state.settings = settings
    app.state.db = db
    app.state.filestore = files
    app.state.redis = fakeredis.FakeRedis()
    app.state.bootstrap_done = True
    lock = threading.Lock()
    client = None

    class Handler(BaseHTTPRequestHandler):
        def respond(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            with lock:
                client.cookies.clear()
                response = client.request(
                    self.command, self.path, headers=dict(self.headers), content=body, follow_redirects=False
                )
            self.send_response(response.status_code)
            for key, value in response.headers.multi_items():
                if key.lower() not in {"content-length", "content-encoding", "transfer-encoding"}:
                    self.send_header(key, value)
            self.send_header("Content-Length", str(len(response.content)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(response.content)

        do_GET = do_POST = do_DELETE = do_PUT = do_PATCH = do_HEAD = respond

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    monkeypatch.setitem(globals(), "ORIGIN", f"http://127.0.0.1:{server.server_port}")
    client = TestClient(app, base_url=ORIGIN, raise_server_exceptions=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    payload = base64.b64encode(json.dumps({"uid": admin.id, "sv": admin.session_version}).encode())
    cookie = TimestampSigner(TEST_SESSION_SECRET).sign(payload).decode()
    yield {"client": client, "cookie": cookie, "job": job.id, "preset": preset.id, "db": db}
    server.shutdown()
    server.server_close()
    thread.join()
    client.close()
    db.close()


@pytest.fixture
def atelier_page(page, atelier_app):
    page.set_default_timeout(10000)
    page.set_default_navigation_timeout(15000)
    state = {"fail": None, "held_path": None, "held": []}

    def fulfill(route):
        request = route.request
        path = urlsplit(request.url).path
        if request.headers.get("x-atelier-navigation") == "1":
            if state["fail"] == path:
                route.abort("failed")
                return
            if state["held_path"] == path:
                state["held"].append(route)
                return
        route.continue_()

    page.route(ORIGIN + "/**", fulfill)
    page.context.add_cookies([{"name": "session", "value": atelier_app["cookie"], "url": ORIGIN}])
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    yield page, state, errors


@pytest.mark.parametrize("viewport", [(1440, 900), (768, 1024), (390, 844)])
def test_all_atelier_screens(atelier_page, atelier_app, viewport, browser_name):
    page, _, errors = atelier_page
    page.set_viewport_size({"width": viewport[0], "height": viewport[1]})
    job = atelier_app["job"]
    paths = [
        "/",
        "/upload",
        "/jobs",
        f"/viewer/{job}",
        f"/studio/{job}",
        "/presets",
        "/presets/new",
        f"/presets/{atelier_app['preset']}/edit",
        "/admin/users",
        "/admin/jobs",
        "/login",
        "/register",
        "/shared/atelier-preview-share",
    ]
    for path in paths:
        if path in {"/login", "/register"} or path.startswith("/shared/"):
            page.context.clear_cookies()
        else:
            page.context.add_cookies([{"name": "session", "value": atelier_app["cookie"], "url": ORIGIN}])
        response = page.goto(ORIGIN + path)
        assert response.status == 200, path
        page.wait_for_function("!!window.Alpine")
        page.wait_for_timeout(100)
        assert page.locator("h1").count() >= 1, path
        for theme in ("whisper-light", "whisper-dark"):
            page.evaluate("theme => document.documentElement.dataset.theme = theme", theme)
            bounds = page.evaluate("({width:innerWidth, scroll:document.documentElement.scrollWidth})")
            assert bounds["scroll"] <= bounds["width"] + 2, (path, viewport, theme, bounds)
        if path == "/upload":
            page.locator('input[name="vad_enabled"][value="false"]').check()
            assert page.locator("form.at2-upload-form").evaluate("el => new FormData(el).get('vad_enabled')") == "false"
            page.locator('input[name="vad_enabled"][value="true"]').check()
            with page.expect_file_chooser() as chooser:
                page.locator('label[for="upload-files"]').focus()
                page.keyboard.press("Enter")
            media = atelier_app["db"].get_job(job).filepath
            chooser.value.set_files(media)
            page.get_by_text("File selezionati", exact=True).wait_for(state="visible")
            assert page.locator("#merge-mode").is_hidden()
            page.locator(".at2-upload-audio summary").click()
            page.locator('select[name="audio_denoise"]').select_option("medium")
            page.locator('input[name="audio_target_lufs"]').fill("-2")
            page.locator(".at2-upload-audio summary").click()
            assert not page.locator("form.at2-upload-form").evaluate("el => el.reportValidity()")
            assert page.locator(".at2-upload-audio").get_attribute("open") is not None
            page.locator('input[name="audio_target_lufs"]').fill("-16")
            page.locator(".at2-upload-audio summary").click()
            assert (
                page.locator("form.at2-upload-form").evaluate("el => new FormData(el).get('audio_denoise')") == "medium"
            )
            page.locator("button").evaluate_all(
                "buttons => buttons.find(b => /tab.*folder/.test(b.getAttribute('@click') || '')).click()"
            )
            with page.expect_file_chooser():
                page.locator('label[for="upload-folder"]').click()
        if path in {"/jobs", "/admin/jobs"}:
            search = page.get_by_role("searchbox")
            search.fill("nessun-risultato-qa")
            page.get_by_text("Nessun risultato in questa pagina", exact=True).wait_for(state="visible")
            assert page.locator(".at2-job-row:visible").count() == 0
            page.get_by_role("button", name="Cancella ricerca", exact=True).click()
            page.locator(".at2-job-row:visible").wait_for(state="visible")
        if path == "/admin/users":
            page.get_by_role("searchbox", name="Cerca un account").fill("qa-reader")
            page.wait_for_function(
                "Array.from(document.querySelectorAll('.admin-table-scroll tbody tr'))"
                ".filter(el=>el.offsetParent!==null).length===1"
            )
            assert page.locator(".admin-table-scroll tbody tr:visible").inner_text().find("qa-reader") >= 0
            page.get_by_role("searchbox", name="Cerca un account").fill("")
        assert not errors, (path, errors)


def test_navigation_network_recovery_and_session_expiry(atelier_page, atelier_app):
    page, state, errors = atelier_page
    page.goto(ORIGIN + "/")
    page.wait_for_function("!!window.AtelierNavigation && !!window.Alpine")
    page.evaluate("window.__shell = document.querySelector('.atelier-sidebar')")
    for path in [
        "/upload",
        "/jobs",
        "/presets",
        "/presets/new",
        f"/viewer/{atelier_app['job']}",
        f"/studio/{atelier_app['job']}",
        "/",
    ]:
        page.evaluate("path => AtelierNavigation.navigate(path)", path)
        page.wait_for_function(
            "path => location.pathname === path && !document.documentElement.classList.contains('atelier-navigating')",
            arg=path,
        )
        assert page.evaluate("window.__shell === document.querySelector('.atelier-sidebar')")
    state["fail"] = "/jobs"
    page.evaluate("AtelierNavigation.navigate('/jobs')")
    page.wait_for_function("!document.documentElement.classList.contains('atelier-navigating')")
    assert urlsplit(page.url).path == "/"
    state["fail"] = None
    page.evaluate("AtelierNavigation.navigate('/jobs')")
    page.wait_for_url(ORIGIN + "/jobs")
    page.go_back()
    page.wait_for_url(ORIGIN + "/")
    page.wait_for_function("!document.documentElement.classList.contains('atelier-navigating')")
    assert not errors
    page.context.clear_cookies()
    page.evaluate("AtelierNavigation.navigate('/upload')")
    page.wait_for_url(re.compile(r".*/login.*"))
    assert page.locator('input[name="password"]').is_visible()


def test_mobile_account_menu_and_login(atelier_page):
    page, _, errors = atelier_page
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(ORIGIN + "/")
    page.locator(".atelier-account-menu summary").click()
    assert page.locator(".atelier-account-panel a[href='/admin/users']").is_visible()
    with page.expect_response(
        lambda response: response.url.endswith("/logout") and response.request.method == "POST"
    ) as logout:
        page.locator(".atelier-account-panel button[type='submit']").click()
    assert logout.value.status in {302, 303}, logout.value.status
    page.wait_for_url(re.compile(r".*/login.*"), timeout=5000)
    page.locator('input[name="username"]').fill("qa-admin")
    password = page.locator('input[name="password"]')
    password.fill("qa-password-123")
    assert password.get_attribute("type") == "password"
    password.locator("..").get_by_role("button").click()
    assert password.get_attribute("type") == "text"
    password.locator("..").get_by_role("button").click()
    assert password.get_attribute("type") == "password"
    page.locator('button[type="submit"]').click()
    page.wait_for_url(ORIGIN + "/")
    assert page.locator(".atelier-account-menu").is_visible()
    assert not errors


def test_studio_edit_and_preset_save(atelier_page, atelier_app):
    page, _, errors = atelier_page
    job = atelier_app["job"]
    page.goto(ORIGIN + f"/studio/{job}")
    editor = page.locator('textarea[aria-label="Testo modificabile della battuta"]').first
    editor.wait_for(state="visible")
    editor.fill("Testo modificato durante la verifica isolata.")
    with page.expect_response(
        lambda response: response.url.endswith("/custom") and response.request.method == "POST"
    ) as saved:
        page.get_by_role("button", name="Salva personalizzata", exact=True).click()
    assert saved.value.status == 200
    files = atelier_app["client"].app.state.filestore
    assert files.load_custom_result(job).segments[0].text == "Testo modificato durante la verifica isolata."
    assert files.load_result(job).segments[0].text == "Prima frase di prova."

    page.set_viewport_size({"width": 390, "height": 844})
    editor.fill("Modifica non salvata durante la revisione.")
    page.get_by_text("Modifiche non salvate", exact=True).wait_for(state="visible")
    page.get_by_role("button", name="Confronta versioni", exact=True).click()
    page.once("dialog", lambda dialog: dialog.dismiss())
    page.get_by_role("button", name="Copia nell'editor", exact=True).first.click()
    page.get_by_role("button", name="Modifica testo", exact=True).click()
    assert editor.input_value() == "Modifica non salvata durante la revisione."
    page.goto(ORIGIN + "/presets/new")
    page.locator('input[name="name"]').fill("Creato dal browser QA")
    page.locator('form[action="/presets"] button[type="submit"]').click()
    page.wait_for_url(ORIGIN + "/presets")
    presets = PresetRepository(atelier_app["db"].conn).list_for_owner(1)
    created = next(preset for preset in presets if preset.name == "Creato dal browser QA")
    page.goto(ORIGIN + f"/presets/{created.id}/edit")
    page.locator('input[name="name"]').fill("Modificato dal browser QA")
    page.locator(f'form[action="/presets/{created.id}/edit"] button[type="submit"]').click()
    page.wait_for_url(ORIGIN + "/presets")
    assert PresetRepository(atelier_app["db"].conn).get(created.id, 1).name == "Modificato dal browser QA"
    assert not errors


def test_viewer_audio_reading_and_frequency_spectrum(atelier_page, atelier_app, browser_name):
    """Use real media decoding and FFT, not mocked player/canvas callbacks."""
    page, _, errors = atelier_page
    page.goto(ORIGIN + f"/viewer/{atelier_app['job']}")
    page.wait_for_function("!!window.Alpine && document.querySelector('.wv-page')._x_dataStack")
    page.wait_for_function("document.querySelector('#viewer-waveform-status').textContent.includes('tocca')")
    response = page.request.get(ORIGIN + f"/viewer/{atelier_app['job']}/waveform")
    assert max(response.json()["peaks"]) > 0.35
    page.get_by_role("searchbox", name="Cerca nella trascrizione").fill("Seconda")
    page.wait_for_function("document.querySelectorAll('.wv-segment[style*=none]').length === 1")
    page.get_by_role("button", name="Paragrafi", exact=True).click()
    page.locator(".wv-paragraph:visible").wait_for(state="visible")
    assert page.locator(".wv-paragraph:visible").count() == 1
    assert page.locator(".wv-paragraph:visible mark").inner_text() == "Seconda"
    page.get_by_role("button", name="Originale Whisper", exact=True).click()
    page.locator(".wv-paragraph:visible").wait_for(state="visible")
    assert page.locator(".wv-paragraph:visible").count() == 1
    page.get_by_role("button", name="Cancella ricerca").click()
    page.get_by_role("button", name="Battute", exact=True).click()
    page.locator(".wv-spectrum-details summary").click()
    page.get_by_role("button", name="Riproduci", exact=True).click()
    page.wait_for_function("document.querySelector('#media-player').currentTime > .15")
    page.wait_for_function("Number(document.querySelector('#viewer-spectrum').dataset.peakHz) > 900")
    peak = page.locator("#viewer-spectrum").get_attribute("data-peak-hz")
    assert 900 < int(peak) < 1100, peak
    page.get_by_role("button", name="Pausa", exact=True).click()
    page.get_by_role("combobox", name="Velocità di riproduzione").select_option("1.5")
    assert page.locator("#media-player").evaluate("el => el.playbackRate") == 1.5
    page.locator("#viewer-seek").evaluate(
        "el => { el.value=1.25; el.dispatchEvent(new Event('input',{bubbles:true})); }"
    )
    page.wait_for_function("Math.abs(document.querySelector('#media-player').currentTime-1.25)<.05")
    page.get_by_role("button", name="Audio elaborato", exact=True).click()
    page.wait_for_function(
        "document.querySelector('#media-player').readyState>=1 && "
        "document.querySelector('#media-player').src.endsWith('processed-audio')"
    )
    assert abs(page.locator("#media-player").evaluate("el => el.currentTime") - 1.25) < 0.1
    page.get_by_role("button", name="Aumenta zoom").click()
    assert float(page.locator("#viewer-seek").get_attribute("max")) < 4
    page.get_by_role("button", name="Tutto", exact=True).click()
    assert float(page.locator("#viewer-seek").get_attribute("max")) == 4
    page.evaluate("window.whisperCopy = async text => { window.__copiedText = text; }")
    page.get_by_role("button", name="Copia testo", exact=True).click()
    assert page.evaluate("window.__copiedText") == "Prima frase di prova.\n\nSeconda frase."
    page.get_by_role("button", name="Esporta", exact=True).click()
    assert page.locator("#viewer-export-dialog").is_visible()
    with page.expect_download():
        page.locator('a[href$="/export/txt"]').click()
    page.keyboard.press("Escape")
    page.evaluate("window.__oldPlayer = document.querySelector('#media-player')")
    page.evaluate("AtelierNavigation.navigate('/jobs')")
    page.wait_for_url(ORIGIN + "/jobs")
    assert page.evaluate("window.__oldPlayer.paused")
    page.evaluate("path => AtelierNavigation.navigate(path)", f"/viewer/{atelier_app['job']}")
    page.wait_for_url(ORIGIN + f"/viewer/{atelier_app['job']}")
    page.get_by_role("button", name="Riproduci", exact=True).click()
    page.wait_for_function("document.querySelector('#media-player').currentTime > .1")
    page.get_by_role("button", name="Pausa", exact=True).click()
    for width in (1440, 768, 390):
        page.set_viewport_size({"width": width, "height": 950 if width > 390 else 844})
        for theme in ("whisper-light", "whisper-dark"):
            page.evaluate("theme => document.documentElement.dataset.theme=theme", theme)
            page.wait_for_timeout(100)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth+2")
            page.evaluate("scrollTo(0,0)")
            page.wait_for_timeout(150)
            page.screenshot(path=f".qa-artifacts/viewer-{browser_name}-{width}-{theme}.png")
    page.get_by_role("button", name="Dettagli audio +", exact=True).click()
    page.locator(".wv-spectrum-details summary").click()
    page.get_by_role("button", name="Riproduci", exact=True).click()
    page.wait_for_function("Number(document.querySelector('#viewer-spectrum').dataset.peakHz) > 900")
    page.get_by_role("button", name="Pausa", exact=True).click()
    page.get_by_role("button", name="Meno dettagli -", exact=True).click()
    page.locator(".wv-document").evaluate("el => el.scrollIntoView({block: 'start'})")
    page.locator(".wv-mini-player").wait_for(state="visible")
    page.get_by_role("button", name="Riprendi audio", exact=True).click()
    page.wait_for_function("!document.querySelector('#media-player').paused")
    page.get_by_role("button", name="Metti in pausa", exact=True).click()
    page.screenshot(path=f".qa-artifacts/viewer-{browser_name}-390-reading.png")
    assert not errors, errors


def test_batch_upload_collection_and_polling(atelier_page, atelier_app, browser_name):
    """Exercise native disclosures, live refresh, search, selection and ZIP export."""
    from datetime import UTC, datetime, timedelta
    from zipfile import ZipFile

    from playwright.sync_api import expect

    from whisper_ui.ui import labels

    page, _, errors = atelier_page
    db = atelier_app["db"]
    files = atelier_app["client"].app.state.filestore
    owner = db.get_job(atelier_app["job"]).owner_id
    now = datetime.now(UTC)
    baseline = db.get_job(atelier_app["job"])
    baseline.created_at = (now - timedelta(days=1)).isoformat()
    db.update_job(baseline)

    def add_batch(key, names, statuses, title=None, age=0):
        jobs = []
        for index, (name, status) in enumerate(zip(names, statuses, strict=True)):
            job = Job(
                filename=name,
                owner_id=owner,
                batch_id=key,
                batch_title=title,
                status=status,
                language="it",
                duration=4,
                created_at=(now - timedelta(seconds=age, microseconds=index)).isoformat(),
                error="Formato audio non leggibile. Riprova con il file originale."
                if status == JobStatus.FAILED
                else None,
            )
            if status == JobStatus.COMPLETED:
                job.result_path = str(
                    files.save_result(
                        job.id,
                        TranscriptResult(
                            segments=[Segment(0, 4, "Testo sintetico del caricamento.", "S1")],
                            duration=4,
                            language="it",
                        ),
                    )
                )
            db.insert_job(job)
            jobs.append(job)
        return jobs

    small_id, mixed_id, large_id = "b" * 32, "c" * 32, "d" * 32
    small = add_batch(
        small_id,
        ["01 - Apertura riunione.wav", "02 - Decisioni del team.wav", "03 - Prossimi passi.wav"],
        [JobStatus.COMPLETED] * 3,
    )
    mixed = add_batch(
        mixed_id,
        ["Intervista pronta.wav", "Intervista da recuperare.wav", "Intervista in corso.wav"],
        [JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.PROCESSING],
        title='Interviste <team> & revisione "finale"',
        age=2,
    )
    add_batch(
        large_id,
        [f"{i:02d} - Lezione e approfondimento della registrazione.wav" for i in range(1, 9)],
        [JobStatus.COMPLETED] * 8,
        title="Corso completo",
        age=4,
    )

    page.goto(ORIGIN + "/jobs")
    small_group = page.locator("#job-group-" + small_id)
    small_details = page.locator("#batch-files-" + small_id)
    mixed_group = page.locator("#job-group-" + mixed_id)
    mixed_details = page.locator("#batch-files-" + mixed_id)
    large_details = page.locator("#batch-files-" + large_id)
    first = page.locator("#job-" + small[0].id)
    second = page.locator("#job-" + small[1].id)
    expect(first.get_by_role("link", name=small[0].filename, exact=True)).to_be_visible()
    assert not large_details.evaluate("el=>el.open")
    first.get_by_role("checkbox").check()
    second.get_by_role("checkbox").check()
    expect(page.get_by_role("button", name="Unisci trascrizioni", exact=True)).to_be_enabled()
    first.locator("details > summary").click()
    expect(first.get_by_role("link", name="Modifica in Studio ↗", exact=True)).to_be_visible()
    small_details.locator(":scope > summary").click()
    expect(first.get_by_role("link", name=small[0].filename, exact=True)).to_be_hidden()

    small[0].filename = "Note aggiornate dopo il polling.wav"
    db.update_job(small[0])
    with page.expect_response(lambda response: urlsplit(response.url).path == "/jobs/list"):
        page.evaluate("htmx.trigger(document.body,'refreshJobList')")
    expect(page.locator("#job-" + small[0].id)).to_have_attribute("data-job-name", small[0].filename)
    assert not small_details.evaluate("el=>el.open")
    search = page.get_by_role("searchbox")
    search.fill("Note aggiornate")
    expect(first.get_by_role("link", name=small[0].filename, exact=True)).to_be_visible()
    assert small_details.evaluate("el=>el.open")
    expect(page.locator(".at2-job-row:visible")).to_have_count(1)
    page.get_by_role("button", name="Cancella ricerca", exact=True).click()
    expect(first.get_by_role("link", name=small[0].filename, exact=True)).to_be_hidden()
    small_details.locator(":scope > summary").click()
    expect(first.get_by_role("checkbox")).to_be_checked()
    expect(second.get_by_role("checkbox")).to_be_checked()
    expect(first.get_by_role("link", name="Modifica in Studio ↗", exact=True)).to_be_visible()
    first.locator("details > summary").click()
    first.get_by_role("checkbox").uncheck()
    second.get_by_role("checkbox").uncheck()

    mixed_details.locator(":scope > summary").click()
    mixed[2].status = JobStatus.COMPLETED
    mixed[2].result_path = str(files.save_result(mixed[2].id, files.load_result(small[1].id)))
    db.update_job(mixed[2])
    with page.expect_response(lambda response: urlsplit(response.url).path == "/jobs/list"):
        page.evaluate("htmx.trigger(document.body,'refreshJobList')")
    expect(mixed_group.get_by_role("button", name="Scarica gruppo", exact=True)).to_be_visible()
    assert not mixed_details.evaluate("el=>el.open")

    page.get_by_role("button", name=re.compile("^" + re.escape(labels.STATUS_LABELS["failed"]))).click()
    expect(mixed_group.get_by_text("1 di 3 file in questa pagina · filtro attivo", exact=True)).to_be_visible()
    expect(mixed_group.get_by_role("progressbar", name="File elaborati nel gruppo")).to_have_attribute(
        "aria-valuemax", "3"
    )
    page.get_by_role("button", name=re.compile("^" + re.escape(labels.JOBS_FILTER_ALL))).click()
    expect(small_group.get_by_role("button", name="Scarica gruppo", exact=True)).to_be_visible()
    small_group.get_by_role("button", name="Scarica gruppo", exact=True).click()
    dialog = page.locator("dialog[open]")
    dialog.locator("select").select_option("txt")
    with page.expect_download() as exported:
        dialog.locator('a[href*="/jobs/batch/"]').click()
    with ZipFile(exported.value.path()) as archive:
        assert len(archive.namelist()) == 3
        assert all(name.endswith(".txt") for name in archive.namelist())

    page.evaluate("AtelierNavigation.navigate('/admin/jobs')")
    page.wait_for_url(ORIGIN + "/admin/jobs")
    row = page.locator("#job-" + small[0].id)
    row.locator("details > summary").click()
    expect(row.get_by_role("link", name="Ritrascrivi", exact=True)).to_have_count(0)
    expect(row.get_by_text("qa-admin", exact=True)).to_be_visible()
    page.evaluate("AtelierNavigation.navigate('/jobs')")
    page.wait_for_url(ORIGIN + "/jobs")

    for width in (1440, 768, 390, 320):
        page.set_viewport_size({"width": width, "height": 950 if width > 390 else 844})
        for theme in ("whisper-light", "whisper-dark"):
            page.evaluate("theme=>Alpine.$data(document.documentElement).theme=theme", theme)
            page.wait_for_timeout(120)
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth+2"), (width, theme)
            page.evaluate("scrollTo(0,0);document.querySelector('.jobs-scroll-panel').scrollTop=0")
            page.screenshot(path=f".qa-batches/{browser_name}-{width}-{theme}.png", animations="disabled")
    assert not errors, errors


@pytest.mark.parametrize('viewport', [(1366, 650), (1600, 1000), (1920, 1080), (428, 926)])
def test_viewport_workspaces(atelier_page, atelier_app, viewport):
    """Real routes with long documents: desktop uses panel scroll, phone stays touch-friendly."""
    page, _, errors = atelier_page
    page.set_viewport_size({'width': viewport[0], 'height': viewport[1]})
    job_id = atelier_app['job']
    app = atelier_app['client'].app
    store = app.state.filestore
    job = atelier_app['db'].get_job(job_id)
    text = '# Riassunto di prova\n\n' + ('Testo sufficientemente lungo per verificare lo scorrimento.\n\n' * 250)
    store.save_summary(job_id, {'text': text, 'source':'corrected','model':'auto-resident'})
    for i in range(22):
        clone = Job(filename=f'Registrazione QA {i}.wav', owner_id=job.owner_id, status=JobStatus.COMPLETED)
        atelier_app['db'].insert_job(clone)
        store.save_summary(clone.id, {'text': text, 'source':'original','model':'auto-resident'})
    long_result = TranscriptResult(segments=[Segment(i*2,i*2+2,f'Battuta di prova numero {i}. '+('Testo di verifica. '*8),'S1') for i in range(75)],language='it',duration=150)
    store.save_result(job_id,long_result)
    store.save_corrected_result(job_id,long_result)
    desktop=viewport[0]>=1000
    paths=[('/', '.at2-home-content'),('/upload','.at-desktop-upload-fields' if desktop else '.at2-upload-main'),('/jobs','.at2-workspace-results'),(f'/viewer/{job_id}','.wv-transcript-scroll'),(f'/studio/{job_id}','.at2-review-columns'),('/presets','.at2-library-panel'),('/presets/new','.at2-editor-stack'),(f'/presets/{atelier_app["preset"]}/edit','.at2-editor-stack'),('/admin/users','.admin-table-scroll'),('/admin/jobs','.at2-workspace-results'),(f'/summaries?job={job_id}&summary=legacy','.summary-panes')]
    for path,panel in paths:
        response=page.goto(ORIGIN+path)
        assert response.status==200,path
        page.wait_for_function('!!window.Alpine')
        page.locator(panel).first.wait_for(state='visible')
        if path.startswith('/summaries'):
            page.locator('#summary-text').wait_for(state='visible')
        metrics=page.evaluate('''selector=>{const s=document.scrollingElement,p=document.querySelector('#page-content'),t=document.querySelector('.atelier-topbar'),r=document.querySelector(selector).getBoundingClientRect();return {window:[innerWidth,innerHeight],body:[s.scrollWidth,s.scrollHeight],shell:[p.clientHeight,p.scrollHeight,p.clientWidth,p.scrollWidth],top:t.getBoundingClientRect().top,panel:[r.top,r.bottom,r.width,r.height]}}''',panel)
        print(viewport,path,metrics)
        assert metrics['body'][0]<=viewport[0]+2,(path,metrics)
        if desktop:
            assert metrics['body'][1]<=viewport[1]+2,(path,metrics)
            assert metrics['shell'][1]<=metrics['shell'][0]+2,(path,metrics)
            assert metrics['shell'][3]<=metrics['shell'][2]+2,(path,metrics)
            assert metrics['panel'][1]<=viewport[1]+2 and metrics['panel'][3]>25,(path,metrics)
            assert metrics['top']==0,(path,metrics)
        if path=='/upload':
            page.locator('input[name="vad_enabled"][value="false"]').check()
            assert page.locator('form.at2-upload-form').evaluate("el=>new FormData(el).get('vad_enabled')")=='false'
            with page.expect_file_chooser():
                page.locator('label[for="upload-files"]').click()
            page.locator('.at2-upload-audio summary').click()
            page.locator('select[name="audio_denoise"]').select_option('medium')
            if desktop:
                assert page.locator('.at2-upload-submit button').evaluate('el=>{const r=el.getBoundingClientRect();return r.top>=0&&r.bottom<=innerHeight}')
        if path.startswith('/summaries'):
            if desktop:
                assert page.locator('.summary-list').evaluate('el=>el.scrollHeight>el.clientHeight')
                assert page.locator('#summary-text').evaluate('el=>el.scrollHeight>el.clientHeight')
                assert page.locator('.summary-markdown').evaluate('el=>el.scrollHeight>el.clientHeight')
                page.locator('.summary-markdown').evaluate('el=>el.scrollTop=500')
                assert page.locator('.summary-markdown').evaluate('el=>el.scrollTop')>0
                assert page.locator('#summary-text').evaluate('el=>el.scrollTop')==0
            page.locator('#summary-text').fill('# Documento aggiornato\n\n'+text)
            page.get_by_role('button',name='Salva',exact=True).click()
            page.get_by_text('Modifiche salvate',exact=True).wait_for()
            assert page.locator('.summary-dochead a').get_attribute('href')==f'/studio/{job_id}'
            page.get_by_role('button',name='Markdown',exact=True).click()
            page.locator('.summary-markdown h1').first.wait_for()
            assert page.locator('.summary-markdown h1').first.inner_text()=='Documento aggiornato'
    for path in ['/login', '/register', '/shared/atelier-preview-share']:
        page.context.clear_cookies()
        response = page.goto(ORIGIN + path)
        assert response.status == 200, path
        page.wait_for_function('!!window.Alpine')
        if desktop:
            metrics = page.evaluate("() => { const e=document.scrollingElement; const panel=document.querySelector('.at2-auth-layout,.at2-share-root'); return [e.scrollWidth,e.scrollHeight,panel.clientHeight,panel.scrollHeight] }")
            assert metrics[0] <= viewport[0]+2 and metrics[1] <= viewport[1]+2, (path, metrics)
            assert metrics[3] <= metrics[2]+2, (path, metrics)
        if path == '/login':
            assert page.locator('input[name="password"]').is_visible()
        if path.startswith('/shared'):
            assert page.locator('.shared-transcript-list').is_visible()
            if desktop:
                assert page.locator('.shared-transcript-list').evaluate('el=>el.scrollHeight>el.clientHeight')
    assert not errors,errors


@pytest.mark.parametrize('viewport', [(1000, 600), (1366, 768), (1600, 1000), (1920, 1080), (428, 926)])
def test_horizontal_desktop_navigation(atelier_page, viewport):
    page, _, errors = atelier_page
    width, height = viewport
    page.set_viewport_size({'width':width,'height':height})
    page.goto(ORIGIN + '/')
    page.wait_for_function('!!window.Alpine && !!window.AtelierNavigation')
    desktop = width >= 1000
    nav = page.locator('.atelier-desktop-nav' if desktop else '.atelier-mobile-nav')
    assert nav.is_visible()
    assert not page.locator('.atelier-sidebar').is_visible()
    assert page.locator('.atelier-desktop-brand').is_visible() == desktop
    if desktop:
        assert not page.locator('.atelier-mobile-nav').is_visible()
        bounds = page.locator('#page-content').bounding_box()
        assert bounds['x'] <= 2 and bounds['width'] >= width-2, bounds
        top = page.locator('.atelier-topbar').bounding_box()
        assert top['y'] == 0 and top['height'] <= 52, top
        links = nav.locator('a').all()
        assert len(links) == 5
        action = page.locator('.atelier-topbar-actions').bounding_box()
        for link in links:
            r = link.bounding_box()
            assert r['x'] >= 0 and r['x']+r['width'] <= action['x']+1, (viewport, r, action)
    else:
        assert not page.locator('.atelier-desktop-nav').is_visible()
    page.evaluate("window.__navbar = document.querySelector('.atelier-topbar')")
    for path in ['/upload','/jobs','/summaries','/presets','/']:
        nav.locator(f'a[href="{path}"]').click()
        page.wait_for_function('path => location.pathname === path && !document.documentElement.classList.contains("atelier-navigating")', arg=path)
        assert nav.locator(f'a[href="{path}"]').get_attribute('aria-current') == 'page'
        assert page.evaluate("window.__navbar === document.querySelector('.atelier-topbar')")
        assert page.evaluate('document.scrollingElement.scrollWidth <= innerWidth + 2')
    page.locator('.atelier-topbar button.atelier-icon-button').click()
    page.locator('.atelier-account-menu summary').click()
    page.locator('.atelier-account-panel a[href="/admin/users"]').click()
    page.wait_for_function('location.pathname === "/admin/users" && !document.documentElement.classList.contains("atelier-navigating")')
    assert not page.locator('.atelier-account-menu').get_attribute('open')
    assert page.locator('.atelier-account-menu').evaluate("el=>el.classList.contains('atelier-account-active')")
    assert not errors,errors
