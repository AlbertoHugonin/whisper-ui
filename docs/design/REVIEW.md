# Rewrite design review

Audit of current UI: repeated uppercase marketing labels and nested cards dilute hierarchy; neon accent and hardcoded values conflict in light/dark modes; nine equal wizard tabs compete with form contents; desktop has narrow text panels despite available space; mobile needs touch-size controls and stable safe areas. Direction: Signal, a compact audio/text workbench. Use the actual data and routes, not a fake mockup. Review both empty and populated states. Keep navigation and form state stable across SPA changes.

## Skill sources (pinned snapshots)
- https://github.com/pbakaus/impeccable @ 2fc5e1459efbbf06d92c7306517c286bf12490cf — instructions/references only, no engine download.
- https://github.com/nextlevelbuilder/ui-ux-pro-max-skill @ 11ca2b3bdd555649f278ee348c32a9302b24dcf1 — local design-system search, no external inference.
- https://github.com/anthropics/skills @ e4bb5d960d87126ff3b3a8af1a104b7db67560ba — frontend-design.
- https://github.com/vercel-labs/agent-skills @ 5a37a6329fbad3666c3207392a6a7c0110e92efb — web-design-guidelines and pinned bundled references.

Scope: UI templates and styling, no replacement of API/database/worker contracts. Static guideline review does not establish full WCAG compliance. Browser tests cover specified scenarios, not physical Safari. Preview uses synthetic data only, no worker or production NAS/Redis/LLM.

## Verification

- 21 isolated browser tests and 5 JavaScript tests passed.
- Live isolated preview: 24 route/viewport screenshots, authenticated and checked for horizontal document overflow and page errors.
- Preview service is LAN-only HTTP on Jarvis VM port 18087, fake Redis, separate synthetic data and no workers. Use demo credentials only; do not upload confidential audio.
