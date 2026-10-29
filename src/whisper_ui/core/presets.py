"""Validated, complete settings snapshots for built-in and personal presets.

The form fields mirror the upload and re-transcribe job fields. Selecting a
preset never changes an existing job; an upload persists its own settings.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from whisper_ui.core.languages import DEFAULT_WHISPER_MODEL, LANGUAGE_CHOICES, WHISPER_MODELS


class PresetSettings(BaseModel):
    language: str = "auto"
    model_name: str = DEFAULT_WHISPER_MODEL
    num_speakers: int = Field(default=0, ge=0, le=20)
    enable_diarization: bool = False
    convert_to_traditional: bool = False
    llm_correction_enabled: bool = False
    whisper_keywords: str = Field(default="Airdass", max_length=1500)
    vad_threshold: float = Field(default=0.25, ge=0.10, le=0.60)
    llm_context_prompt: str = Field(default="", max_length=4000)
    audio_processing: Literal["auto", "off", "custom"] = "auto"
    audio_denoise: Literal["auto", "off", "light", "medium"] = "auto"
    audio_compression: Literal["auto", "off", "light", "medium", "strong"] = "auto"
    audio_target_lufs: float = Field(default=-16.0, ge=-24, le=-12)
    audio_max_gain_db: float = Field(default=18.0, ge=0, le=30)
    audio_highpass_hz: int = Field(default=80, ge=0, le=180)

    model_config = {"extra": "forbid"}

    @field_validator("language", "model_name")
    @classmethod
    def check_upload_choices(cls, value: str, info):
        valid = LANGUAGE_CHOICES if info.field_name == "language" else WHISPER_MODELS
        if value not in valid:
            raise ValueError(f"Unsupported {info.field_name}: {value}")
        return value


BUILTIN_PRESETS = {
    "meeting": {
        "name": "Meeting", "description": "Riunioni, più interlocutori e correzione LLM",
        "settings": PresetSettings(enable_diarization=True, llm_correction_enabled=True).model_dump(),
    },
    "podcast": {
        "name": "Podcast", "description": "Due interlocutori e correzione LLM",
        "settings": PresetSettings(enable_diarization=True, num_speakers=2, llm_correction_enabled=True).model_dump(),
    },
    "interview": {
        "name": "Intervista", "description": "Interviste a due, diarizzazione e correzione",
        "settings": PresetSettings(enable_diarization=True, num_speakers=2, llm_correction_enabled=True).model_dump(),
    },
    "lecture": {
        "name": "Lezione", "description": "Voce singola e correzione LLM",
        "settings": PresetSettings(llm_correction_enabled=True).model_dump(),
    },
}
