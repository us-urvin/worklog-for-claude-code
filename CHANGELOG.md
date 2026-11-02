# Changelog

All notable changes to this project are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.5.0] - 2026-11-02

### Added
- **Trends** page: hours per week or month, the change against the same days before, a few plain sentences worked
  out on your computer, when you worked, projects and kind of work.
- **Ask your worklog**: an instant search on your computer (understands "last week", "October", PR numbers), and
  **Ask Claude** for a short written answer from your saved notes, never your code. `claude-worklog ask`.
- **Where you left off** on Today: your last three projects with what you were doing, PRs and tickets, branch and
  open tasks.
- **On your phone**: a summary page in your private data repo (`phone/README.md`) for the GitHub app, written by
  one computer after each sync. `claude-worklog digest`.
- **Share cards** for a day, week, month or year (totals only; PNG download), **badges**, and **your year in review**.
- **Goals**: hours per day and a weekly token limit, with rings on Today.
- **More on each card**: PR and ticket numbers (linked; tickets via Settings → Links and cost), test runs and fixes,
  Claude's time against yours, and an estimate at API prices.

### Changed
- **Faster pages for weeks and months**: a small per-day cache (about 2 KB a day) is read instead of every record,
  and a day is only read again when it changed.
- **A new README, GIF and video**: grouped by what you get every day, every week and anywhere, with a steady
  40-second tour built from still screens with captions (the old 97-second screen recording flickered).
- **Dropdowns, checkboxes and date fields** follow the theme in dark and light mode. The top menu scrolls sideways on
  phones.

### Fixed
- Settings were saved to the default config path instead of the file that was loaded (only mattered with a custom
  config path).

## [0.4.1] - 2026-11-02

### Fixed
- **Timesheet lines** no longer show a short prompt such as "Approve" or an unrelated first question. Days summarized
  before 0.4.0 use the first point of their summary until you press **Rewrite summaries**; without any summary, the
  first prompt that says something is used.
- **A summary written for other sessions is no longer shown on a card.** After 0.4.0 moved a session to its real
  project (for example a PR review into **xoo**), the old summary stayed under "R&D / misc". Summaries now record
  which sessions they cover; a card says when its summary is out of date.
- **Tags**: only Claude's choice (made with the summary, now with clear rules for each kind of work) or yours is
  shown. The keyword guess was often wrong, so it's gone.

### Added
- `claude-worklog doctor` warns when the web page still runs an older version after an update, and prints the
  restart command for your system. Troubleshooting has a new entry for it.

## [0.4.0] - 2026-11-02

### Added
- **Timesheet** page and `claude-worklog timesheet`: one row per day and project with hours, the kind of work and a
  one-line summary. This or last week or month, or custom days; filter by account; round to 15 or 30 minutes. Download
  Excel (.xlsx) or CSV, or copy as a table or Markdown. **Write missing summaries** fills the gaps.
- **Summaries in plain words**: a short paragraph a manager or client can read, a timesheet line, details and
  suggested next steps, from the same single `claude -p` call per project as before.
- **Kind of work** tags on every card (Feature, Bug fix, Review, Refactor, Docs, Tests, Research, Planning, Release,
  Chat), guessed from your prompts and files, suggested by Claude with the summary, and yours to change.
- **Claude's to-do lists** on the cards (TodoWrite and the task tools), with **Copy checklist** and **Copy as text**.
- **✏️ Edit** on each card: rename a project, move all or some sessions to another or a new project, add a rule for
  a folder you always start in, set the kind of work or the timesheet line, and undo. Saved in the data repo
  (`overrides/<device>.json`) so every computer agrees; records are never changed.

### Changed
- **Finds the project of sessions started outside a repo** (for example in `~/development`): from other folders the
  session ran in, the files it edited, the folders its commands worked in, or `gh -R owner/repo`. When two repos are
  equally likely, the card asks **"Probably …?"**.
- **Worktrees show under the repo's name**, so `xoo-fix-qty` counts as **xoo**.
- **Time is active time**: breaks over 15 minutes and parallel sessions no longer inflate hours. Older days are marked
  "≈". `[report] time = "wall"` restores first-to-last-message time.

### Fixed
- System messages such as `<task-notification>` no longer show up as your prompts, also on days already recorded.

## [0.3.2] - 2026-11-02

### Fixed
- **Sync on computers without a git e-mail.** Downloading changes failed with "Committer identity unknown" when
  `git config --global user.email` wasn't set, so nothing was uploaded either. Worklog now uses its own neutral
  name for that step too.
- **Changes left waiting now upload.** Commits from an earlier failed sync are pushed on the next sync, even when
  there is nothing new.
- **SSH nicknames** (`git@worklog-git:you/repo.git` from `~/.ssh/config`) no longer give a broken link like
  `https://worklog-git/...`. Settings links to the real host, checks privacy there, and shows which key file is used.
- **Update now** no longer says "already the newest version" right after a release. It skips pip's saved
  version list (`--no-cache-dir`), and if PyPI is still publishing the new version it says to try again in a few minutes.
- The "new version is out" notice waits until a release is about 15 minutes old, so **Update now** can really get it.
  A release that is still settling is checked again after an hour instead of the next day.
- The update command shown on the page and in the docs is now `pipx upgrade --pip-args=--no-cache-dir worklog-for-claude-code`.

### Changed
- **Buttons no longer reload the page or jump to the top.** Sync, Test connection, Write summaries, saving settings
  and the rest work in place: a small card shows progress and the result, and the page keeps its scroll position.
- **Popups for things that need a decision or a clear answer:** Forget a login opens its confirmation in a popup,
  Remove account asks first, and Test connection shows its result in a popup.
- **Test connection says which account signs in** (GitHub, GitLab, Bitbucket), and if that account can't see the
  repo it says so. A new *Several GitHub accounts on this computer?* help in Settings explains the SSH-key setup,
  and connection errors show the one line that matters.
- Finished-job messages (like "Updated 178 session record(s)") close by themselves after 2 minutes (errors after 15),
  and each one has a ✕ to close it right away.
- Upgraded from a terminal while Worklog was running? The page says the new version is installed and offers
  **Restart now**, instead of quietly running the old version until the next login.

## [0.3.1] - 2026-11-02

### Added
- **Forget a login** (Settings → Logins seen on this device → Forget…): deletes Worklog's records of that login's
  sessions on this device, the saved summaries of those projects, and the account linked only to that login, after a
  confirmation that lists exactly what will be lost. Forgotten sessions are never collected again; Claude Code's own
  files are never touched.
- **Settings → Private data repo**: connected or local only, which repo (linked), whether it is really private
  (GitHub repos are checked; a public repo is flagged on every page), when the last upload worked or why it failed,
  how many changes wait to upload, and a **Test connection** button.

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

[Unreleased]: https://github.com/us-urvin/worklog-for-claude-code/compare/v0.5.0...HEAD
[0.5.0]: https://github.com/us-urvin/worklog-for-claude-code/compare/v0.4.1...v0.5.0
[0.4.1]: https://github.com/us-urvin/worklog-for-claude-code/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/us-urvin/worklog-for-claude-code/compare/v0.3.2...v0.4.0
[0.3.2]: https://github.com/us-urvin/worklog-for-claude-code/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/us-urvin/worklog-for-claude-code/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/us-urvin/worklog-for-claude-code/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/us-urvin/worklog-for-claude-code/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/us-urvin/worklog-for-claude-code/releases/tag/v0.1.0
