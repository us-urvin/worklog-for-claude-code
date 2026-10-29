# Changelog

All notable changes to this project are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0] - 2026-10-29

Everything since 0.2.0 (0.2.1 was never released on its own).

### Added
- **macOS and Windows**, not just Linux. `install-service` uses systemd, launchd, or the Windows Startup list
  plus Task Scheduler (no admin rights). New `claude-worklog uninstall-service`.
- **Right account for every session**, even without the hook: while Worklog runs it notices account switches
  (by `/login` or any one-click account app) and new Claude folders, and shows a notice with a one-click fix.
- **Pick the model that writes your summaries** (Settings → Summaries; Opus gives the best ones).
- **Setup checklist** with the exact command for your system, shown until everything works.
- **Update notice**: when a new version is out, the dashboard says so and can update itself with one click.
- **Redesigned, animated dashboard**: stat tiles, a 14-day chart, "When you worked", the last 5 weeks with
  your streak, projects today and a model donut. Light and dark mode, phone sizes, reduced motion respected.
- **Fun mode** (switch in Settings): streak flame, a daily fun fact, confetti for records, a sleepy robot on
  empty days and a hidden party mode.
- "By model" shows readable names, each model's share and how much came from sub-agents.
- claude.ai chats: optional claude.ai summary per chat, each chat shown on its own, chat days marked in the chart.
- `doctor` shows each account's default model, the summary model and whether Worklog starts at login.
- After `install-service`, a one-time question whether you'd like to star the project on GitHub.
- Beginner guide, troubleshooting page, screenshots and a short video.

### Changed
- Settings explains both multi-account setups in one place; a login alone in its own folder no longer asks for
  a name.
- Records use "/" paths and LF line endings everywhere, so all systems can share one data repo.

### Fixed
- Summaries ran on the account's default model (often Sonnet) instead of a model you choose.
- Naming or renaming a login only moved the last 2 days of sessions; it now moves the last 30.
- Name fields in forms used a pattern current browsers reject, so their checks were skipped.

## [0.2.0] - 2026-10-04

### Added
- Import claude.ai chats from claude.ai's data export (`conversations-*.zip`): `claude-worklog import-chats`,
  an upload button on the new **Sources** page, or an opt-in toggle that imports new exports from your Downloads
  folder on every sync. Chats appear on the day page and in the report next to Claude Code work, without token
  counts (the export has none).
- Only chat titles and short redacted snippets of your own messages are kept. Claude's replies, chat summaries,
  thinking, attachments and the export's other files are never read into records.
- Re-importing is idempotent, older exports never overwrite newer data, multi-part exports are merged, chats that
  are already-recorded Claude Code sessions are skipped, and the same chat imported on two devices counts once.
- **Sources** page in the web UI: Claude Code status per account, chat import status and settings, and a
  placeholder for a future browser extension.
- `[chat]` config section: `account`, `watch_downloads`, `downloads_dir`.
- Demo data now includes a few claude.ai chats.

### Changed
- README privacy section reworded to match what the code does: redaction is best effort, and prompts are
  kept up to 300 characters, so a pasted snippet can end up in the data repo.

### Fixed
- README links (guide, security, contributing, changelog, license) now work on the PyPI project page.

## [0.1.0] - 2026-09-26

First public release, published on PyPI as `worklog-for-claude-code`. The command is `claude-worklog`.

### Added
- Collector that turns local Claude Code transcripts (`<config_dir>/projects/**/*.jsonl`) into small, redacted
  per-session JSON records and syncs them through a private git repo shared by all your devices.
- Daily Markdown report: token usage per account and per model, a work summary per project (repos matched by
  their remote across machines), and a separate "R&D / misc" section for work outside git repos.
- Optional AI summaries through `claude -p`, with a plain prompt listing as the fallback.
- Local web UI on loopback (`http://worklog.localhost:8765`): day view with a 14-day token chart, settings,
  account and hook management. Protected with a Host-header check, CSRF tokens, Origin checks and a strict CSP.
- systemd user units: always-on web UI, hourly collect timer, nightly report timer (`install-service`).
- `SessionStart`/`SessionEnd` hooks (`install-hook`). They back up `settings.json` first and are idempotent.
- Multiple accounts, in separate config folders or sharing one folder (matched by the login recorded at session
  start). Only account names reach the data repo; account ids and e-mails stay on the device.
- `doctor` command with masked e-mails, safe to paste into issues.
- `demo` command: runs the web UI on a temporary HOME with generated fake data (2 accounts, 2 devices, 14 days).
- Best-effort redaction of API keys, tokens, JWTs, private keys, `password=` pairs, credentials in URLs,
  e-mail addresses and IPv4 addresses in prompts and commit subjects.

[Unreleased]: https://github.com/us-urvin/worklog-for-claude-code/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/us-urvin/worklog-for-claude-code/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/us-urvin/worklog-for-claude-code/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/us-urvin/worklog-for-claude-code/releases/tag/v0.1.0
