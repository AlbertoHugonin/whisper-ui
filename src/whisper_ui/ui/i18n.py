"""Minimal global UI locale selection.

The upstream project is intentionally kept simple: UI text is selected once at
process startup through ``UI_LANGUAGE``. English is the generic default and
Traditional Chinese remains available for upstream-compatible deployments.
"""

from __future__ import annotations

import os

SUPPORTED_UI_LANGUAGES = {"en", "zh"}
DEFAULT_UI_LANGUAGE = "en"


def resolve_ui_language(value: str | None = None) -> str:
    raw = value if value is not None else os.getenv("UI_LANGUAGE", DEFAULT_UI_LANGUAGE)
    normalized = (raw or DEFAULT_UI_LANGUAGE).strip().lower().replace("_", "-")
    base = normalized.split("-", 1)[0]
    return base if base in SUPPORTED_UI_LANGUAGES else DEFAULT_UI_LANGUAGE
