"""Private, SQLite-backed transcription presets on the existing web DB."""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import sqlite3

    from whisper_ui.core.presets import PresetSettings


@dataclass(frozen=True)
class SavedPreset:
    id: str
    owner_id: int
    name: str
    description: str
    settings: dict
    created_at: str
    updated_at: str


def _preset(row: sqlite3.Row) -> SavedPreset:
    return SavedPreset(
        id=row['id'], owner_id=row['owner_id'], name=row['name'],
        description=row['description'], settings=json.loads(row['settings_json']),
        created_at=row['created_at'], updated_at=row['updated_at'],
    )


class PresetRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def list_for_owner(self, owner_id: int) -> list[SavedPreset]:
        rows = self.conn.execute(
            'SELECT * FROM transcription_presets WHERE owner_id=? ORDER BY name COLLATE NOCASE',
            (owner_id,),
        ).fetchall()
        return [_preset(row) for row in rows]

    def get(self, preset_id: str, owner_id: int) -> SavedPreset | None:
        row = self.conn.execute(
            'SELECT * FROM transcription_presets WHERE id=? AND owner_id=?',
            (preset_id, owner_id),
        ).fetchone()
        return _preset(row) if row else None

    def create(self, owner_id: int, name: str, description: str, settings: PresetSettings) -> SavedPreset:
        now = datetime.now(UTC).isoformat()
        pid = uuid.uuid4().hex
        self.conn.execute(
            'INSERT INTO transcription_presets (id,owner_id,name,description,settings_json,created_at,updated_at) '
            'VALUES (?,?,?,?,?,?,?)',
            (pid, owner_id, name, description, settings.model_dump_json(), now, now),
        )
        self.conn.commit()
        return self.get(pid, owner_id)

    def update(self, preset_id: str, owner_id: int, name: str, description: str, settings: PresetSettings) -> bool:
        result = self.conn.execute(
            'UPDATE transcription_presets SET name=?,description=?,settings_json=?,updated_at=? '
            'WHERE id=? AND owner_id=?',
            (name, description, settings.model_dump_json(), datetime.now(UTC).isoformat(), preset_id, owner_id),
        )
        self.conn.commit()
        return result.rowcount > 0

    def delete(self, preset_id: str, owner_id: int) -> bool:
        result = self.conn.execute(
            'DELETE FROM transcription_presets WHERE id=? AND owner_id=?',
            (preset_id, owner_id),
        )
        self.conn.commit()
        return result.rowcount > 0
