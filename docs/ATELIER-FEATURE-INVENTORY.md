# Atelier · feature inventory and safe rollout

## Current production architecture

- Source: `AlbertoHugonin/whisper-ui`, branch `main`. Atelier is the sole UI.
- Canonical URL: `https://whisper.home.albertohugonin.it`; the former
  `atelier.home.albertohugonin.it` hostname redirects to the canonical host.
- Frontend: `whisper-hub-frontend` on apps-server (`192.168.0.120:8085`).
  Redis remains `whisper-hub-redis`; SQLite and identities remain in
  `whisper-hub_app-data`, with uploads/outputs on NAS under `whisper-live`.
- The distributed workers and internal callbacks continue using port 8085.
  There is no second production frontend or separate preview library.
- Keep `/internal/worker/` inaccessible on public hostnames and preserve
  Authentik protection and HTTPS-only application sessions.
- Compose and Komodo resources are versioned in `homelab-infra`. The former
  all-in-one UI stack on ai-server is no longer a deployment option.

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

## Interaction repair and lean QA (2026-11-05)

- Fixed a malformed duplicate Upload layout section (duplicate presets, progress
  overlay and mislocated event attributes) and restored a single source panel.
- The submission bar now stays in the normal document flow so it cannot cover
  editable fields or intercept taps; file inputs are not hit-test targets.
  Explicit labeled file/folder buttons are the only picker activators.
- Upgraded Atelier stylesheet asset version to `at2-r2` to invalidate cached CSS.
- Simplified Atelier-specific unit tests from three modules (11 assertions/tests)
  into one three-test fast smoke module. Existing cross-cutting JS regressions
  remain; broad screenshot sweeps are not a default requirement.
- Executed a targeted Chromium interaction check with real Alpine/HTMX loaded
  over 12 screens at 1440×900 PC and 430×932 iPhone, 24 page checks total.
  Confirmed clickable controls, no document horizontal overflow and no browser
  runtime errors. Verified 6 non-picker interactions and 2 intended
  file/folder picker activations on each viewport. Screens and test data were
  synthetic; no production files or jobs were created.

## Atelier desktop workstation + SPA-like navigation

Atelier provides an HTMX-driven client router
(`static/atelier-router.js`) that fetches only the selected `#page-content`
from existing server-rendered pages. It preserves desktop and phone navigation,
header, Alpine theme and toast stores. It maintains browser history, link
queries, title, keyboard focus, and redirects on auth expiry. Native POSTs,
downloads, file pickers and external links bypass this router.

**Security detail:** the initial page's CSP nonce is reused via htmx's
`inlineScriptNonce` option for page-local scripts loaded from HTMX fragment
responses. Upload and jobs use re-entrant `var` declarations so revisits do
not raise top-level `const` redeclaration errors. Viewer correction polling is
stopped on navigation, as is the Studio summary poll when the Alpine component
is destroyed. The production worker/API layer is unchanged.

Desktop (`min-width:1280px` CSS, not actual physical inches) has its own
spacing, sidebar, multi-column controls, and separate 16:9 / 16:10 viewport
density rules, with an intermediate 1100–1279px laptop layout. Phone retains
full functionality but hides nonessential guide panels and reduces decorative
spacing; safe areas and touch targets are preserved.

**Fast validation:** existing Atelier smoke/JS tests plus targeted Chromium
navigation for 1366×768, 1440×810, 1600×1000, 1920×1080 and 430×932;
verified no full document reload, no horizontal overflow, page scripts, and
back navigation. Test pages were synthetic; production end-to-end authenticated
transcription and physical Safari device testing are separate tasks.

## Direct transcription without Silero VAD

Upload shows two explicit engine modes above advanced settings:
**Standard · Silero VAD** (default) and **Diretta · senza Silero**.
Both are available for file/folder/URL inputs and new transcript versions.
The choice submits `vad_enabled=true/false` through the existing upload API;
workers already honor `job.vad_enabled` when constructing `whisper-cli`.
The direct option omits the `--vad` CLI flags and Silero model resolution,
but does not disable audio resampling, the transcription model, or downstream
alignment/correction stages. Without VAD, silence hallucinations are more
likely; it is an opt-in per job and is also configurable in saved presets.
A selected preset can update the radio cards without losing Alpine state.

Browser interaction checks (synthetic preview, no production jobs): 1366×768
16:9, 1600×1000 16:10, 430×932 iPhone; all passed direct/standard
selection, submitted FormData, preset roundtrip and viewport overflow.
