"""Simple opt-in ROCm admission check for a personally managed GPU worker.

A *point-in-time* check, not a reservation: other processes may allocate VRAM
later. The worker is pinned to a single GPU separately in its entrypoint.
"""
from __future__ import annotations

import os
from typing import Callable

from whisper_ui.core.exceptions import TranscriptionError

MIB = 1024 * 1024


def ensure_free_vram(minimum_mib: int, memory_info: Callable[[], tuple[int, int]] | None = None) -> None:
    if minimum_mib <= 0:
        return
    if memory_info is None:
        try:
            import torch

            if not torch.cuda.is_available() or torch.cuda.device_count() != 1:
                raise RuntimeError('il worker non vede esattamente una GPU ROCm')
            memory_info = lambda: torch.cuda.mem_get_info(0)
        except Exception as exc:
            raise TranscriptionError(f'Controllo memoria GPU non disponibile: {exc}') from exc
    try:
        free_bytes, total_bytes = memory_info()
    except Exception as exc:
        raise TranscriptionError(f'Impossibile leggere la VRAM della GRE: {exc}') from exc
    if not (0 <= free_bytes <= total_bytes and total_bytes > 0):
        raise TranscriptionError('Lettura VRAM non valida: trascrizione non avviata.')
    free_mib = free_bytes // MIB
    if free_mib < minimum_mib:
        raise TranscriptionError(
            f'Memoria GPU insufficiente sulla RX 7900 GRE: {free_mib} MiB liberi, '
            f'ne servono almeno {minimum_mib} MiB. Riprova quando la GPU è più libera.'
        )


def check_rocm_vram_before_transcription(device: str) -> None:
    if device != 'rocm':
        return
    raw = os.environ.get('WHISPER_MIN_FREE_VRAM_MIB', '0')
    try:
        minimum = int(raw)
        if not 0 <= minimum <= 16384:
            raise ValueError
    except ValueError as exc:
        raise TranscriptionError('Soglia minima VRAM di Whisper non valida.') from exc
    ensure_free_vram(minimum)
