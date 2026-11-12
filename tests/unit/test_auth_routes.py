"""Login, register, logout endpoint behaviour: bootstrap, validation, errors."""

from __future__ import annotations

import fakeredis
import pytest
from fastapi.testclient import TestClient

from tests.conftest import authed_test_client, make_session_cookie
from whisper_ui.storage import users_repo
from whisper_ui.web.app import create_app


@pytest.fixture
def app(settings, db, filestore):
    application = create_app()
    application.state.settings = settings
    application.state.db = db
    application.state.filestore = filestore
    # FakeRedis (rather than MagicMock) so rate_limit.is_locked /
    # check_and_increment exercise the real INCR / EXPIRE / GET path.
    application.state.redis = fakeredis.FakeRedis()
    application.state.bootstrap_done = True
    return application


def _anon_client(app):
    client = TestClient(app, follow_redirects=False)
    client.headers["Origin"] = "http://testserver"
    return client


def test_login_post_with_correct_credentials_sets_session(app, test_user):
    client = _anon_client(app)

    resp = client.post(
        "/login",
        data={"username": "alice", "password": "password123"},
    )

    assert resp.status_code == 302
    assert resp.headers["location"] == "/"
    assert "session" in resp.cookies or any(c.name == "session" for c in client.cookies.jar)


def test_login_post_with_wrong_password_returns_to_login_with_error(app, test_user):
    client = _anon_client(app)

    resp = client.post(
        "/login",
        data={"username": "alice", "password": "wrongpassword"},
    )

    assert resp.status_code == 302
    assert resp.headers["location"].startswith("/login?error=invalid")


def test_login_post_with_unknown_username_returns_same_error(app):
    client = _anon_client(app)

    resp = client.post(
        "/login",
        data={"username": "ghost", "password": "password123"},
    )

    assert resp.status_code == 302
    # Same error code as wrong-password so existence is not leaked.
    assert resp.headers["location"].startswith("/login?error=invalid")


def test_login_inactive_user_with_correct_password_returns_inactive(app, db, test_user):
    """A legitimate user whose admin disabled their account sees the
    "account inactive" message — but only when they prove identity via
    the correct password.
    """
    users_repo.set_active(db.conn, test_user.id, active=False)
    client = _anon_client(app)

    resp = client.post(
        "/login",
        data={"username": "alice", "password": "password123"},
    )

    assert resp.status_code == 302
    assert resp.headers["location"].startswith("/login?error=inactive")


def test_login_next_parameter_only_accepts_relative_path(app, test_user):
    client = _anon_client(app)

    resp = client.post(
        "/login",
        data={"username": "alice", "password": "password123", "next": "http://evil.example/x"},
    )

    assert resp.status_code == 302
    assert resp.headers["location"] == "/"  # absolute next was discarded


def test_login_next_parameter_rejects_backslash_open_redirect(app, test_user):
    client = _anon_client(app)

    # "/\evil.example" passes a naive startswith("//") check but browsers
    # normalise "\" to "/", resolving it off-site. It must be discarded.
    resp = client.post(
        "/login",
        data={"username": "alice", "password": "password123", "next": "/\\evil.example"},
    )

    assert resp.status_code == 302
    assert resp.headers["location"] == "/"


def test_login_htmx_response_uses_hx_redirect(app, test_user):
    client = _anon_client(app)
    client.headers["HX-Request"] = "true"

    resp = client.post(
        "/login",
        data={"username": "alice", "password": "password123"},
    )

    assert resp.status_code == 204
    assert resp.headers["hx-redirect"] == "/"


def test_register_first_account_becomes_admin(app, db):
    client = _anon_client(app)

    resp = client.post(
        "/register",
        data={"username": "founder", "password": "password123"},
    )

    assert resp.status_code == 302
    assert resp.headers["location"] == "/"

    created = users_repo.get_user_by_username(db.conn, "founder")
    assert created is not None
    assert created.is_admin is True
    assert app.state.bootstrap_done is True


def test_register_rate_limited_per_ip_after_threshold(app, db, test_admin):
    """Open registration is bounded per IP; the attempt past the cap is refused."""
    app.state.settings.max_register_attempts_per_ip = 2
    client = _anon_client(app)

    for name in ("userone", "usertwo"):
        resp = client.post("/register", data={"username": name, "password": "password123"})
        assert resp.status_code == 302  # both consume a per-IP slot

    blocked = client.post("/register", data={"username": "userthree", "password": "password123"})
    assert blocked.status_code == 302
    assert blocked.headers["location"] == "/register?error=rate_limited"
    # The over-limit attempt is rejected before any account is created.
    assert users_repo.get_user_by_username(db.conn, "userthree") is None


def test_register_post_refused_when_signup_closed(app, db, test_admin):
    """A hand-crafted POST cannot create an account once signup is closed."""
    app.state.settings.allow_registration = False
    client = _anon_client(app)

    resp = client.post("/register", data={"username": "intruder", "password": "password123"})

    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"
    assert users_repo.get_user_by_username(db.conn, "intruder") is None


def test_register_second_account_is_not_admin(app, db, test_admin):
    client = _anon_client(app)

    resp = client.post(
        "/register",
        data={"username": "bob", "password": "password123"},
    )

    assert resp.status_code == 302

    created = users_repo.get_user_by_username(db.conn, "bob")
    assert created is not None
    assert created.is_admin is False


def test_register_duplicate_username_returns_generic_error(app, db, test_user, test_admin):
    """Registering a taken username must NOT confirm the account exists: the
    redirect carries a generic 'unavailable' code, not 'username_taken', so
    /register is not an enumeration oracle."""
    client = _anon_client(app)

    resp = client.post(
        "/register",
        data={"username": "alice", "password": "differentpw"},
    )

    assert resp.status_code == 302
    assert resp.headers["location"].startswith("/register?error=unavailable")
    assert "username_taken" not in resp.headers["location"]


def test_register_short_password_returns_error(app, test_admin):
    client = _anon_client(app)

    resp = client.post(
        "/register",
        data={"username": "newuser", "password": "short"},
    )

    assert resp.status_code == 302
    assert resp.headers["location"].startswith("/register?error=password_short")


def test_logout_clears_session_and_redirects(app, test_user):
    client = authed_test_client(app, test_user)

    resp = client.post("/logout", follow_redirects=False)

    assert resp.status_code == 302
    assert resp.headers["location"] == "/login"


def test_login_does_not_log_password(app, test_user, caplog):
    import logging

    caplog.set_level(logging.DEBUG, logger="whisper_ui.web.routes.auth_routes")
    client = _anon_client(app)

    client.post("/login", data={"username": "alice", "password": "secretvalue42"})
    client.post("/login", data={"username": "alice", "password": "wrongpasswordsecret"})

    # Neither the right nor wrong password may appear in any log message.
    combined = " ".join(record.getMessage() for record in caplog.records)
    assert "secretvalue42" not in combined
    assert "wrongpasswordsecret" not in combined


def test_login_rate_limit_blocks_at_threshold(app, test_user):
    """settings.max_login_attempts failed logins must block attempt N+1.

    With default max_attempts=5, after exactly 5 failures the next attempt
    (even with the correct password) is rejected with rate_limited.
    """
    client = _anon_client(app)
    settings = app.state.settings
    # Exactly max_attempts failures — counter ends at max_attempts.
    for _ in range(settings.max_login_attempts):
        client.post("/login", data={"username": "alice", "password": "wrong"})

    # The next attempt (correct password) must bounce.
    resp = client.post(
        "/login",
        data={"username": "alice", "password": "password123"},
    )

    assert resp.status_code == 302
    assert resp.headers["location"].startswith("/login?error=rate_limited")


def test_login_rate_limit_resets_on_successful_login(app, test_user):
    client = _anon_client(app)
    settings = app.state.settings
    # 4 fails (one under the limit).
    for _ in range(settings.max_login_attempts - 1):
        client.post("/login", data={"username": "alice", "password": "wrong"})

    # Successful login clears the per-user counter.
    ok = client.post(
        "/login",
        data={"username": "alice", "password": "password123"},
    )
    assert ok.status_code == 302
    assert ok.headers["location"] == "/"

    # After the reset, a fresh fail-burst should still be allowed.
    fresh = client.post("/login", data={"username": "alice", "password": "wrong"})
    assert fresh.status_code == 302
    assert fresh.headers["location"].startswith("/login?error=invalid")


def test_client_ip_ignores_xff_when_proxy_headers_untrusted(app, db, test_user):
    """Without TRUST_PROXY_HEADERS=true, X-Forwarded-For must be ignored
    to prevent a hostile client from spoofing its IP and evading rate limits.
    """
    client = _anon_client(app)
    client.headers["X-Forwarded-For"] = "1.2.3.4"
    redis = app.state.redis

    client.post("/login", data={"username": "alice", "password": "wrong"})

    # The IP counter should be keyed on the test client's real IP
    # ("testclient" in starlette TestClient), not on the spoofed value.
    assert redis.get("auth:rl:ip:1.2.3.4") is None
    keys = [k.decode() for k in redis.keys("auth:rl:ip:*")]
    assert any("testclient" in k or "127" in k or "unknown" in k for k in keys), keys


def test_client_ip_uses_rightmost_trusted_xff_hop_when_proxy_headers_trusted(app, db, test_user, monkeypatch):
    """With TRUST_PROXY_HEADERS=true and a single trusted proxy, the client IP
    is the RIGHT-most XFF entry (the one our proxy appended), not the
    attacker-controlled left-most one. A spoofed prefix must not create its own
    bucket, so per-IP rate limiting cannot be evaded by rotating XFF values.
    """
    monkeypatch.setattr(app.state.settings, "trust_proxy_headers", True)
    monkeypatch.setattr(app.state.settings, "trusted_proxy_count", 1)
    client = _anon_client(app)
    # Attacker prepends a forged 1.2.3.4; our trusted proxy appends the real 10.0.0.1.
    client.headers["X-Forwarded-For"] = "1.2.3.4, 10.0.0.1"
    redis = app.state.redis

    client.post("/login", data={"username": "alice", "password": "wrong"})

    assert int(redis.get("auth:rl:ip:10.0.0.1") or 0) == 1
    assert redis.get("auth:rl:ip:1.2.3.4") is None, "forged left-most XFF must not get its own bucket"


def test_change_password_invalidates_existing_session(app, db, test_user):
    """End-to-end equivalent of "admin reset" — bump session_version, then
    confirm an existing session is no longer valid.
    """
    # Establish a session by hand-built cookie.
    client = _anon_client(app)
    client.cookies.set("session", make_session_cookie(test_user))

    # First request: should work.
    first = client.get("/", follow_redirects=False)
    assert first.status_code == 200

    # Bump session_version (admin's "set new password" would do this).
    users_repo.set_password(db.conn, test_user.id, "newpassword")

    # Second request: stale session, should redirect to /login.
    second = client.get("/", follow_redirects=False)
    assert second.status_code == 302
    assert second.headers["location"].startswith("/login?next=")
