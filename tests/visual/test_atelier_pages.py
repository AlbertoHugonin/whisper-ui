"""Exercise the actual Atelier pages and navigation using an isolated app/database.

Browser requests use a localhost HTTP bridge to TestClient. No production accounts, files,
Redis servers, workers or external inference endpoints are used.
"""

from __future__ import annotations

import base64
import json
import re
import threading
import wave
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
        audio.writeframes(b"\x00\x00" * 64000)
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
            with page.expect_file_chooser():
                page.locator('label[for="upload-files"]').click()
            page.locator("button").evaluate_all(
                "buttons => buttons.find(b => /tab.*folder/.test(b.getAttribute('@click') || '')).click()"
            )
            with page.expect_file_chooser():
                page.locator('label[for="upload-folder"]').click()
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
    page.locator('input[name="password"]').fill("qa-password-123")
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
