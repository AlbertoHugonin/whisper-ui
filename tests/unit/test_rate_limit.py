"""Tests for the rate-limit decision logging.

The integration-level "does the limit actually block requests?" coverage
lives in tests/unit/test_auth_routes.py; this file focuses narrowly on
the log messages because those are what a security operator will grep
during an incident.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import fakeredis
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from whisper_ui.web.rate_limit import (
    check_and_increment,
    is_locked,
)


@pytest.fixture
def redis():
    return fakeredis.FakeStrictRedis()


@pytest.fixture
def down_redis():
    """A Redis client whose every call raises, simulating an outage."""
    client = MagicMock()
    client.get.side_effect = RedisConnectionError("down")
    client.delete.side_effect = RedisConnectionError("down")
    client.pipeline.side_effect = RedisConnectionError("down")
    return client


def _call_check(redis, username: str = "alice", ip: str = "1.2.3.4") -> bool:
    return check_and_increment(
        redis,
        username=username,
        ip=ip,
        max_user_attempts=5,
        max_ip_attempts=20,
        window_seconds=900,
    )


# --- Fail-open on Redis outage: auth must not 500 when the queue store is down ---


def test_check_and_increment_fails_open_on_redis_error(down_redis, caplog):
    with caplog.at_level(logging.WARNING, logger="whisper_ui.web.rate_limit"):
        blocked = _call_check(down_redis)

    assert blocked is False
    assert any("failing open" in r.message for r in caplog.records)


def test_is_locked_fails_open_on_redis_error(down_redis):
    assert is_locked(down_redis, username="alice", ip="1.2.3.4", max_user_attempts=5, max_ip_attempts=20) is False
