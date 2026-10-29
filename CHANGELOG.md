# Changelog

All notable changes to this project are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.4.0] - 2026-10-29

### Added
- **Runs on macOS and Windows**, not just Linux. `install-service` uses launchd on macOS, and the per-user
  Startup list plus Task Scheduler on Windows (no admin rights needed); missed runs catch up after sleep on
  every system. New `claude-worklog uninstall-service`.
- **Right account without the hook.** While Worklog runs, a watcher notices every account switch (by
  `/login` or any one-click account-switching app) and gives sessions the login that was active when they
  started. It never guesses for switches at the same moment as a session start, or for times it wasn't
  running. Records say how their account was found (`account_source`).
- The watcher also finds Claude folders that aren't accounts yet and hooks another tool removed, and shows a
  notice with a one-click fix. `[watch] auto_setup` does it without asking.
- **Setup checklist**: until Worklog is fully set up, every page shows "Get Worklog ready" with what's
  missing and the exact command for your system (Copy button). Afterwards only a small warning appears if a
  step breaks. Settings always has the full list plus optional tips.
- **Animated dashboard**: new charts (when you worked, the last 5 weeks with your streak, projects today,
  a model donut), counting numbers and gentle entrance animations; reduce-motion settings are respected.
- **Fun mode** (on by default, Settings): streak flame, a daily fun fact, a sleepy robot on empty days,
  confetti for records and milestones, and a hidden party mode.
- `doctor` says whether Worklog starts at login and spots a service that points to a removed Python.

### Changed
- Paths in records always use "/" and files are written with LF line endings; the data repo gets a
  `.gitattributes`. Mac, Windows and Linux devices can share one data repo.
- Hook commands are quoted, so install paths with spaces work.

### Fixed
- File locks and the hook's background sync no longer depend on Linux-only features.

## [0.3.0] - 2026-10-29

### Added
- **Choose the model that writes your summaries**: Settings → Summaries → *Model for summaries* (Opus, Sonnet,
  Haiku, Fable or a model id), saved as `[report] model` and passed to `claude -p --model`. Before, summaries
  always used the account's default model, often Sonnet, which made them less accurate.
- **By model** shows readable names (Opus 5.5), each model's share and how much came from sub-agents.
  New records carry `subagent_tokens_by_model`; older records still show, without the sub-agent split.
- `claude-worklog doctor` prints each account's default model and the model used for summaries.
- A step-by-step guide for people new to the terminal (`docs/GETTING-STARTED.md`), a troubleshooting page
  (`docs/TROUBLESHOOTING.md`), a 1-minute video (`docs/media/worklog-demo.mp4`) and new screenshots.
- Every page has a footer with the version and links to the project, help and bug reports (plain links,
  nothing is loaded from outside).

### Changed
- New look: dark aurora background with frosted-glass cards, stat tiles for the day, gradient buttons; a
  matching light mode follows your system. Still fully offline under the same Content-Security-Policy.
- Settings: accounts and detected logins are one section that explains both setups (one folder per account, or
  one folder with `/login`). A login that is the only one in its own folder no longer asks for a name. A
  warning shows when the summary account shares its folder with another account.
- The README is rewritten in plain language, with who it's for, a 3-step install and common questions.

### Fixed
- Naming or renaming a login only moved its sessions from the last 2 days; older days stayed under the
  wrong account. It now moves the last 30 days right away.
- Name fields used an HTML pattern that current browsers reject, so the browser skipped its own checks.

## [0.2.1] - 2026-10-05

### Added
- Optional: keep claude.ai's own summary of each chat (`[chat] keep_summaries`, a checkbox on the Sources page,
  off by default). Shortened to 600 characters and redacted; turning it off removes them again.
- Days with claude.ai chats get a dot in the 14-day chart (and a chat count in its tooltip), the Sources page
  lists them, an empty day links to your latest chats, and the import message names the latest chat day.

### Changed
- Each claude.ai chat is shown on its own, with its title, time and either its summary or your first messages,
  instead of one merged list of prompts. Untitled chats are named after your first message.
- AI summaries ("Write summaries") get each chat's title, messages and kept summary, so they describe what was done.

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

[Unreleased]: https://github.com/us-urvin/worklog-for-claude-code/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/us-urvin/worklog-for-claude-code/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/us-urvin/worklog-for-claude-code/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/us-urvin/worklog-for-claude-code/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/us-urvin/worklog-for-claude-code/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/us-urvin/worklog-for-claude-code/releases/tag/v0.1.0
