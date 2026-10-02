"""English pipeline/worker/export messages."""

from __future__ import annotations

DOWNLOAD_EXTRACTING_INFO = "Fetching media information..."
DOWNLOAD_IN_PROGRESS = "Downloading audio..."
DOWNLOAD_GDRIVE_IN_PROGRESS = "Downloading file from Google Drive..."
DOWNLOAD_DONE = "Audio download complete."
DOWNLOAD_TWITTER_RESTRICTED = (
    "Unable to download this post. It may require login, be restricted, or be an unsupported X Live/Spaces source."
)
DOWNLOAD_SOURCE_TRANSIENT = "The source is temporarily unavailable or rate-limited. Please try again later."

PREPROCESS_CONVERTING = "Converting audio to 16 kHz mono WAV..."
PREPROCESS_DONE = "Audio preprocessing complete."

TRANSCRIBE_LOADING = "Loading transcription model..."
TRANSCRIBE_RUNNING = "Transcribing audio..."
TRANSCRIBE_DONE = "Transcription complete."

ALIGN_LOADING = "Loading alignment model..."
ALIGN_RUNNING = "Aligning timestamps..."
ALIGN_DONE = "Alignment complete."
ALIGN_SKIPPED = "Alignment failed; using unaligned timestamps."

DIARIZE_LOADING = "Loading speaker diarization model..."
DIARIZE_RUNNING = "Running speaker diarization..."
DIARIZE_RUNNING_HEARTBEAT = "Running speaker diarization ({elapsed} seconds elapsed)..."
DIARIZE_DONE = "Speaker diarization complete."
DIARIZE_SKIPPED = "Speaker diarization skipped (HF token not configured)."
DIARIZE_SKIPPED_DISABLED = "Speaker diarization skipped (disabled by user)."

ASSIGN_RUNNING = "Assigning speakers to segments..."
ASSIGN_DONE = "Speaker assignment complete."
ASSIGN_SKIPPED = "Speaker assignment skipped."
ASSIGN_FAILED = "Speaker assignment failed; using unassigned segments."

POSTPROCESS_RUNNING = "Post-processing transcript..."
POSTPROCESS_DONE = "Post-processing complete."
POSTPROCESS_EMPTY = "No transcript result to post-process."

LLM_CORRECTION_RUNNING = "Running LLM transcript correction ({done}/{total})..."
LLM_CORRECTION_DONE = "LLM transcript correction complete."
LLM_CORRECTION_SKIPPED = "LLM transcript correction skipped."
LLM_CORRECTION_DEGRADED = "LLM transcript correction partially failed; original text was kept for chunks: "

PIPELINE_COMPLETE = "Complete"
RESULT_PERSIST_FAILED = "Failed to save result"
QUALITY_WARNING_REPETITIVE = (
    "Transcript quality warning: {percent}% of {total} segments are repeated, "
    "possibly caused by silence or music hallucinations."
)
EXPORT_DOCX_HEADING = "Transcript"
