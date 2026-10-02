"""Locale-selected shared messages for pipeline, worker, and export layers."""

from __future__ import annotations

from importlib import import_module

from whisper_ui.ui.i18n import resolve_ui_language

UI_LANGUAGE = resolve_ui_language()
_source = import_module(f"whisper_ui.core.messages_{UI_LANGUAGE}")

for _name in dir(_source):
    if _name.isupper():
        globals()[_name] = getattr(_source, _name)

__all__ = [name for name in globals() if name.isupper()]
