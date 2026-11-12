# Focused regression suite

The default suite covers application behavior and failure boundaries. Keep it
compact: reuse an existing workflow test before adding a new case. Avoid tests
that merely assert CSS classes, source-code strings, private helper structure,
log wording, constant values or dataclass defaults.

## Coverage

- Login, registration, sessions, CSRF, administrator actions and job ownership.
- Uploads and URL intake, retries, batches, presets and transcript revisions.
- Original/corrected/custom transcript separation and safe exports.
- Database/file integrity, remote worker authentication and pipeline fencing.
- GPU/VAD selection, failed stages and optional LLM fallbacks.
- Real Atelier pages and user actions on desktop, tablet and phone.

## Commands

```bash
uv sync --extra dev --frozen
uv run pytest -q
uv run pytest -m integration -q
for test_file in tests/js/*.cjs; do node "$test_file"; done
```

Browser checks use an isolated localhost application, disposable SQLite,
FakeRedis and synthetic media; they do not connect to production workers.

```bash
npm ci
mise run css
uv run playwright install --with-deps chromium
uv run pytest tests/visual/test_atelier_pages.py -m visual --browser chromium -q
```

For a release pass, also install WebKit and add `--browser webkit`. The browser
checks cover the main screens, responsive/light/dark layouts, upload pickers,
navigation errors, expired sessions, mobile account actions, transcript edits
and preset saves. They also exercise search clearing, account filtering,
single-file selection, folded audio settings and unsaved edits in the mobile Studio. The viewer workflow checks real audio playback, seeking,
source switching, text search/copy/export and a 1 kHz signal through the live
frequency analyser. Integration checks preserve MP3/AAC waveform amplitudes. Screenshots are a diagnostic tool, not a growing repository
of implementation snapshots.
