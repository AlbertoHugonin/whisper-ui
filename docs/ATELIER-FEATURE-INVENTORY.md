# Atelier · feature inventory and safe rollout

## Scope and architecture

- Source: `AlbertoHugonin/whisper-ui`, branch `feat/whisper-atelier-ui`.
- Existing hub: `whisper-hub-frontend` on `apps-server` (`192.168.0.120:8085`). Redis `whisper-hub-redis`, SQLite in `whisper-hub_app-data`; uploads/outputs on NAS at `whisper-live`.
- Production GPU/LLM workers stay in the existing distributed pipeline. Remote-worker internal callback API remains pinned to **original** hub port 8085; no second worker starts.
- Atelier: separate frontend container with a separate port, same existing volume/Redis/identity. `WHISPER_SECONDARY_FRONTEND=true` prevents duplicate stale-job recovery and retention cleanup tasks. SQLite WAL/busy timeout support concurrent frontend clients. The new frontend is *not* a staging database and actions update the same library and queues; avoid treating it as an isolated test environment.
- Never expose `/internal/worker/` over either public hostname. Keep Authentik auth_request + app login + HTTPS-only session. New hostname must have its own TLS certificate.
- Deployment must not restart or replace the existing `whisper-hub` or GPU workers.

## Feature-by-feature inventory (from route/template implementation)

| Group | Capabilities and implementation |
| --- | --- |
| Upload | Multiple audio/video files; directory import; selected-file ordering; URL intake; YouTube/X/Google Drive sources and supported playlists; batch jobs and progress; size/duration limits; validation and error feedback. `upload.html`, `routes/upload.py`. |
| Transcription | Model/language selection; automatic language detection; prompt/keywords; VAD controls (Silero), preprocessing choices; diarization on capable workers; precision/model options; re-transcription without losing original versions. `upload.html`, `routes/jobs.py`, pipeline. |
| AI postprocessing | Configurable LLM transcript correction, correction retry, stand-alone correction and summary; original vs corrected versions. `routes/viewer.py`, `routes/studio.py`, worker tasks. |
| Library/jobs | Search filename/source URL; status filters/counts; bulk selection/delete/export; retry of failures; batch retry and batch ZIP; completed jobs reordering/merging with transcript variants and optional audio merge. `routes/jobs.py`, `routes/merge_jobs.py`. |
| Viewer | Synchronized audio/video playback, zoomable waveform and overview, segment/time seeking, transcript segments and speaker labels, correction version switching, sharing and revocation. `viewer.html`, `routes/viewer.py`. |
| Review Studio | Advanced player, zoom and time navigation, compare versions, manual editing saved as custom version; generate summary; export custom transcript. `studio.html`, `routes/studio.py`. |
| Export | TXT, SRT, VTT, JSON, DOCX and batch ZIP; processed/original audio when available. `export/`, viewer/jobs. |
| Presets | Built-in scenes, private custom presets, create/edit/delete, apply to upload, model/LLM/VAD/audio controls. `routes/presets.py`. |
| Access | Login/logout, registration gate, multi-user ownership isolation, shared token viewer, per-user jobs/presets. `auth_routes.py`, `auth.py`, `routes/shared.py`. |
| Admin | User creation/activation/deactivation/password reset/admin role; job supervision and ownership visibility. `routes/admin.py`. |
| Runtime | Live pipeline progress/ETA, polling, GPU/CPU/IO/LLM stage queues, distributed remote workers and wake/lease supervisor, retention/recovery, metrics and diagnostics. `dashboard.py`, `metrics.py`, infra. |

## New UI system

- **Atelier** is a new visual shell and dashboard, with a distinct CSS design system, without changing backend endpoints or Alpine/HTMX action contracts.
- Desktop: persistent navigation, contextual header, action-first home, library and workspaces. Tablet: condensed icon rail. Phone: independent bottom navigation + compact header.
- iPhone 15 Pro Max nominal viewport: 430 × 932 CSS pixels. Layout also accounts for 320px phones, 390px phones, safe-area insets, 100dvh, 16px form inputs (avoid iOS zoom), touch-friendly controls, reduced motion, keyboard focus, light/dark themes.
- Existing upload, library, viewer, studio, presets and admin behaviors are retained while their common visual controls/tokens are remapped in `atelier.css`; enhanced screen-by-screen refinements remain possible.

## Validation requirements

1. Template syntax, HTML navigation targets, theme variables and UI CSS link in standalone auth/shared pages.
2. Existing JS component tests and relevant server-side regression tests.
3. Container smoke tests: `/login` and static Atelier CSS, public proxy authentication, `/internal/worker` blocked, and original production endpoint still healthy.
4. Viewport/device-level visual QA: desktop 1440px/1920px, iPhone 430px and narrower 390px/320px, especially upload drag/drop, long filenames, modal export dialogs and transcript scrolling.

## Atelier v2 (full page architecture refactor)

The `feat/whisper-atelier-ui` branch now provides independent layouts for all
12 primary screens: dashboard, upload, jobs, viewer, studio, presets, preset
editor, admin users, admin jobs, login, registration, and public token viewer.
The design primitives, page layouts, reusable job list/search/status controls
and export dialogs are styled in `atelier-v2.css`. The existing route,
form-name, Alpine, HTMX and worker callback interfaces remain unchanged.

Non-destructive preview QA generated 12 synthetic page renderings against the
actual Jinja templates (without production user data) and ran Chromium
Playwright in 36 viewport cases: 1440×900 desktop, 430×932 iPhone 15 Pro Max,
375×812 narrow phone. All pages loaded with CSS and no document horizontal
scroll; additional descendant overflow tests discovered and fixed the long
filename row, preset editor grid and viewer breadcrumb. 8 pre-existing Atelier
unit checks, 3 new visual contract checks and all 3 client JS tests passed.

**Scope of validation:** synthetic previews exercise layout and DOM, not real
Apple Safari hardware or authenticated end-to-end job processing. The public
login, CSS, Authentik gate, existing worker API restrictions, and old hub health
must be smoke tested again after upgrading only the Atelier container.
