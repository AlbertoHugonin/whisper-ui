from __future__ import annotations

import importlib

from whisper_ui.ui.i18n import resolve_ui_language


def test_resolve_ui_language_defaults_to_english(monkeypatch):
    monkeypatch.delenv("UI_LANGUAGE", raising=False)
    assert resolve_ui_language() == "en"


def test_resolve_ui_language_accepts_chinese_variants():
    assert resolve_ui_language("zh") == "zh"
    assert resolve_ui_language("zh-TW") == "zh"
    assert resolve_ui_language("zh_CN") == "zh"


def test_resolve_ui_language_falls_back_to_english():
    assert resolve_ui_language("fr") == "en"
    assert resolve_ui_language("") == "en"


def test_labels_default_to_english(monkeypatch):
    monkeypatch.setenv("UI_LANGUAGE", "en")
    import whisper_ui.ui.labels as labels

    importlib.reload(labels)
    assert labels.NAV_DASHBOARD == "Dashboard"
    assert "detected language" in labels.UPLOAD_LLM_CORRECTION_HELP


def test_labels_keep_traditional_chinese(monkeypatch):
    monkeypatch.setenv("UI_LANGUAGE", "zh")
    import whisper_ui.ui.labels as labels

    importlib.reload(labels)
    assert labels.NAV_DASHBOARD == "首頁"
    # Restore the process-global module for the upstream compatibility suite.
    monkeypatch.setenv("UI_LANGUAGE", "zh")
    importlib.reload(labels)
