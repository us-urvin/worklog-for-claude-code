# Changelog

All notable changes to this project are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/us-urvin/worklog-for-claude-code/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/us-urvin/worklog-for-claude-code/releases/tag/v0.1.0
