"""Manage user's custom presets; built-in presets are immutable templates."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError

from whisper_ui.core.languages import LANGUAGE_CHOICES, LANGUAGE_LABELS, WHISPER_MODELS
from whisper_ui.core.presets import BUILTIN_PRESETS, PresetSettings
from whisper_ui.storage.presets import PresetRepository
from whisper_ui.web.deps import CurrentUserDep, DbDep, SettingsDep, templates

router = APIRouter()


def _repo(db: DbDep) -> PresetRepository:
    return PresetRepository(db.conn)


def _resolve_source(repo: PresetRepository, owner_id: int, key: str) -> dict:
    if not key:
        return PresetSettings(whisper_keywords="").model_dump()
    if key in BUILTIN_PRESETS:
        return BUILTIN_PRESETS[key]['settings'].copy()
    source = repo.get(key, owner_id)
    if source is None:
        raise HTTPException(status_code=404, detail='Preset non trovato')
    return source.settings.copy()


def _editor(
    request: Request, settings: SettingsDep, cfg: dict, *, preset=None, error: str = '',
    form_name: str | None = None, form_description: str | None = None,
):
    return templates.TemplateResponse(
        request=request, name='preset_editor.html',
        context={
            'active_page': 'presets', 'preset': preset, 'cfg': cfg,
            'error': error, 'builtins': BUILTIN_PRESETS,
            'form_name': form_name if form_name is not None else (preset.name if preset else ''),
            'form_description': (form_description if form_description is not None
                                 else (preset.description if preset else '')),
            'supported_languages': LANGUAGE_CHOICES, 'language_labels': LANGUAGE_LABELS,
            'whisper_models': WHISPER_MODELS,
            'diarization_available': settings.diarization_available,
            'llm_correction_available': settings.llm_correction_available,
        },
    )


@router.get('/presets', response_class=HTMLResponse)
async def preset_list(request: Request, db: DbDep, user: CurrentUserDep):
    return templates.TemplateResponse(request=request, name='presets.html', context={
        'active_page': 'presets', 'presets': _repo(db).list_for_owner(user.id),
        'builtins': BUILTIN_PRESETS,
    })


@router.get('/presets/new', response_class=HTMLResponse)
async def preset_new(request: Request, db: DbDep, user: CurrentUserDep, settings: SettingsDep, source: str = ''):
    return _editor(request, settings, _resolve_source(_repo(db), user.id, source))


@router.get('/presets/{preset_id}/edit', response_class=HTMLResponse)
async def preset_edit(preset_id: str, request: Request, db: DbDep, user: CurrentUserDep, settings: SettingsDep):
    preset = _repo(db).get(preset_id, user.id)
    if preset is None:
        raise HTTPException(status_code=404)
    return _editor(request, settings, preset.settings, preset=preset)


async def _save(request: Request, db: DbDep, user: CurrentUserDep, settings: SettingsDep, preset_id: str | None):
    repo = _repo(db)
    previous = repo.get(preset_id, user.id) if preset_id else None
    if preset_id and previous is None:
        raise HTTPException(status_code=404)
    form = await request.form()
    name = str(form.get('name', '')).strip()
    description = str(form.get('description', '')).strip()
    raw = {key: form[key] for key in PresetSettings.model_fields if key in form}
    for key in ('enable_diarization', 'convert_to_traditional', 'llm_correction_enabled'):
        raw[key] = key in form
    try:
        if not (1 <= len(name) <= 70):
            raise ValueError('Il nome deve essere lungo da 1 a 70 caratteri')
        if len(description) > 300:
            raise ValueError('La descrizione è troppo lunga')
        cfg = PresetSettings.model_validate(raw)
        if previous:
            repo.update(previous.id, user.id, name, description, cfg)
        else:
            repo.create(user.id, name, description, cfg)
    except (ValidationError, ValueError, sqlite3.IntegrityError) as exc:
        cfg = PresetSettings().model_dump()
        cfg.update(raw)
        return _editor(request, settings, cfg, preset=previous,
                       error='Nome già in uso o impostazioni non valide: ' + str(exc)[:450],
                       form_name=name, form_description=description)
    return RedirectResponse('/presets', status_code=303)


@router.post('/presets')
async def preset_create(request: Request, db: DbDep, user: CurrentUserDep, settings: SettingsDep):
    return await _save(request, db, user, settings, None)


@router.post('/presets/{preset_id}/edit')
async def preset_update(preset_id: str, request: Request, db: DbDep, user: CurrentUserDep, settings: SettingsDep):
    return await _save(request, db, user, settings, preset_id)


@router.post('/presets/{preset_id}/delete')
async def preset_delete(preset_id: str, db: DbDep, user: CurrentUserDep):
    if not _repo(db).delete(preset_id, user.id):
        raise HTTPException(status_code=404)
    return RedirectResponse('/presets', status_code=303)
