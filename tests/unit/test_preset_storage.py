"""Database and validation regression tests for persistent private presets."""
from __future__ import annotations

import sqlite3

import pytest
from pydantic import ValidationError

from whisper_ui.core.presets import BUILTIN_PRESETS, PresetSettings
from whisper_ui.storage.presets import PresetRepository


def test_builtin_presets_contain_all_upload_fields():
    expected = set(PresetSettings.model_fields)
    assert len(BUILTIN_PRESETS) == 4
    for preset in BUILTIN_PRESETS.values():
        assert set(preset['settings']) == expected
        assert PresetSettings.model_validate(preset['settings'])


def test_invalid_preset_settings_are_rejected():
    for key, value in [
        ('vad_min_speech_ms', -5), ('vad_min_silence_ms', 20000),
        ('vad_speech_pad_ms', 6000), ('vad_samples_overlap', 1.5),
        ('vad_threshold', .05), ('whisper_beam_size', 6), ('num_speakers', 999), ('audio_processing', 'infinite'),
        ('audio_target_lufs', -80), ('llm_context_prompt', 'X' * 4001),
        ('model_name', 'not-a-model'), ('language', 'not-a-language'),
        ('summary_prompt', 'x' * 4001),
    ]:
        with pytest.raises(ValidationError):
            PresetSettings.model_validate({key: value})
    with pytest.raises(ValidationError):
        PresetSettings.model_validate({'arbitrary_setting': 'not-allowed'})


def test_create_update_delete_are_owner_isolated(db):
    repo = PresetRepository(db.conn)
    cfg = PresetSettings(language='it', vad_threshold=.15, whisper_keywords='Airdass, Progetto')
    original = repo.create(1, 'Meeting Airdass', 'Per la squadra', cfg)
    assert original.settings['vad_threshold'] == .15
    assert repo.get(original.id, 2) is None
    assert repo.list_for_owner(2) == []
    assert not repo.update(original.id, 2, 'Hacked', '', PresetSettings())
    assert not repo.delete(original.id, 2)
    assert repo.get(original.id, 1).name == 'Meeting Airdass'
    assert repo.update(original.id, 1, 'Meeting progetto', 'Nuova', PresetSettings(vad_threshold=.25))
    assert repo.get(original.id, 1).settings['vad_threshold'] == .25
    assert repo.get(original.id, 1).name == 'Meeting progetto'
    with pytest.raises(sqlite3.IntegrityError):
        repo.create(1, 'MEETING PROGETTO', '', cfg)
    assert repo.delete(original.id, 1)
    assert repo.get(original.id, 1) is None


def test_new_database_connection_retains_preset(db):
    repo = PresetRepository(db.conn)
    preset = repo.create(1, 'Persistente', '', PresetSettings(llm_context_prompt='Airdass e clienti'))
    second = sqlite3.connect(db._db_path)
    second.row_factory = sqlite3.Row
    try:
        restored = PresetRepository(second).get(preset.id, 1)
        assert restored.settings['llm_context_prompt'] == 'Airdass e clienti'
    finally:
        second.close()
