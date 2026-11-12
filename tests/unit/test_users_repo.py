from __future__ import annotations

import sqlite3

import pytest

from whisper_ui.storage import users_repo
from whisper_ui.storage.users_repo import LastAdminError


@pytest.fixture
def conn(db) -> sqlite3.Connection:
    """A connection to an initialised, in-memory-style temp DB."""
    return db.conn


def test_create_user_collation_treats_username_case_insensitively(conn):
    users_repo.create_user(conn, "Alice", "password123")

    with pytest.raises(sqlite3.IntegrityError):
        users_repo.create_user(conn, "alice", "password123")


def test_get_user_by_id_returns_none_for_missing_id(conn):
    assert users_repo.get_user_by_id(conn, 9999) is None


def test_set_password_bumps_session_version(conn):
    user = users_repo.create_user(conn, "alice", "password123")

    users_repo.set_password(conn, user.id, "newpassword")

    updated = users_repo.get_user_by_id(conn, user.id)
    assert updated is not None
    assert updated.session_version == user.session_version + 1
    assert users_repo.verify_password(updated, "newpassword") is True
    assert users_repo.verify_password(updated, "password123") is False


def test_set_active_cannot_deactivate_last_active_admin(conn):
    admin = users_repo.create_user(conn, "root", "password123", is_admin=True)

    with pytest.raises(LastAdminError):
        users_repo.set_active(conn, admin.id, active=False)


def test_set_admin_cannot_demote_last_active_admin(conn):
    admin = users_repo.create_user(conn, "root", "password123", is_admin=True)

    with pytest.raises(LastAdminError):
        users_repo.set_admin(conn, admin.id, admin=False)
