# Guide

Everything in detail. For the short version, see the [README](../README.md).
New to the terminal? Start with the [step-by-step guide](GETTING-STARTED.md). Something broken? See [Troubleshooting](TROUBLESHOOTING.md).

**Words used on this page**

| Word | Meaning |
|---|---|
| **Token** | The unit Claude counts text in. More tokens means more of your plan's usage. |
| **Hook** | A setting in Claude Code that tells Worklog when a session starts and ends. |
| **Data repo** | The git folder where your log is saved. Put it on GitHub/GitLab (private!) to share it between computers. |
| **Deploy key** | A key that lets one computer write to one repo, without your password. |
| **Service** | A program that runs in the background and starts by itself when you log in (systemd on Linux, launchd on macOS, the Startup list and Task Scheduler on Windows). |
| **Watcher** | The part of Worklog that notices account switches and new Claude folders while it runs. |
| **Device** | One of your computers. Each one gets its own name. |

- [Full setup (several machines)](#full-setup)
- [Multiple accounts](#multiple-accounts)
- [Command line](#command-line)
- [Privacy in detail](#privacy)
- [Limitations](#limitations)
- [Uninstall](#uninstall)
- [Troubleshooting](TROUBLESHOOTING.md)

## Full setup

About 5 minutes per machine, one time.

**1. Create the data repo (once).** On GitHub, GitLab or Bitbucket, create an **empty, private** repo, for example `claude-worklog-data`.

**2. Give each machine non-interactive access to it.** Background services have no SSH agent, so use a **deploy key with write access, scoped to this one repo**:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/claude_worklog -N "" -C "claude-worklog $(hostname)"
cat ~/.ssh/claude_worklog.pub     # add this as a deploy key (write access) on the data repo
cat >> ~/.ssh/config <<'CFG'
Host worklog-git
  HostName github.com          # or gitlab.com / bitbucket.org
  User git
  IdentityFile ~/.ssh/claude_worklog
  IdentitiesOnly yes
CFG
ssh -T worklog-git              # accept the host key once
```

**3. Install and start it:**

```bash
sudo apt update && sudo apt install -y pipx git
pipx ensurepath && source ~/.bashrc
pipx install worklog-for-claude-code
claude-worklog install-service               # on ONE machine (it builds the nightly summaries)
claude-worklog install-service --no-report   # on every other machine
```

**4. Open <http://worklog.localhost:8765>** and finish setup: pick a unique device name, set the data repo (paste `worklog-git:<you>/claude-worklog-data.git` and it is cloned for you), and add your first account. Then press **Install hook** for each account in Settings.

Already started with a local-only repo from the quick start? Connect it with `git -C ~/claude-worklog-data remote add origin worklog-git:<you>/claude-worklog-data.git`. The next sync pushes to it.

`install-service` sets up three things that start whenever you log in. No admin rights are needed on any system:

| What | Linux (systemd user units) | macOS (launchd agents) | Windows |
|---|---|---|---|
| The web UI at `worklog.localhost:8765` (loopback only; restarts if it crashes on Linux and macOS) | `claude-worklog-web.service` | `dev.worklog.web` | "Worklog for Claude Code" in the Startup list |
| Hourly sync, a backstop for sessions whose hook didn't fire | `claude-worklog-collect.timer` | `dev.worklog.collect` | Task Scheduler `\Worklog\collect` |
| Summaries at 23:30, re-run at 00:20 for late work (one machine only) | `claude-worklog-report.timer` | `dev.worklog.report` | `\Worklog\report`, `\Worklog\report-late` |

A run missed while the computer was asleep or off happens when it wakes, on every system. On Linux, to keep the services running when you aren't logged in (for example on a desktop you reach over SSH), run `sudo loginctl enable-linger $USER`. `claude-worklog doctor` says whether Worklog starts at login, and warns if the service points to a Python that no longer exists (run `install-service` again after a Python upgrade).

`*.localhost` names resolve to your own machine in Chrome, Edge, Firefox and systemd-resolved, so no `/etc/hosts` edit is needed. Safari may not resolve them: use `http://localhost:8765` there. `http://localhost:8765` works too. For another port: `claude-worklog install-service --port 9000`.

**Summaries and the model.** The daily summaries run `claude -p` with the account chosen under **Settings → Summaries**. Pick the **Model for summaries** there (Opus, Sonnet, Haiku, Fable or a model id such as `claude-opus-5-5`). It is saved as `model` in the `[report]` section and passed as `claude -p --model …`. Left on *Account default*, summaries use that account's default model. The background service doesn't see shell aliases or `ANTHROPIC_MODEL`, so set the model here.

The config file is `~/.config/claude-worklog/config.toml`. The UI edits it for you; `claude-worklog init` writes a commented example.

## Multiple accounts

Everything is on the **Settings** page, under **Claude accounts**. First pick the setup you use:

| You start accounts like this | Setup |
|---|---|
| `claude` for one, `CLAUDE_CONFIG_DIR=~/.claude-work claude` for another | **One folder per account** (below) |
| always plain `claude`, switching with `/logout` and `/login` | **Several accounts in one folder** (below) |

Using both is fine too.

**One folder per account.** In the UI, open **Settings → Add an account**, name it (for example `work`), then log in to it once in a terminal. The UI shows the exact command:

```bash
CLAUDE_CONFIG_DIR=~/.claude-work claude    # then type /login
```

Then press **Install hook** next to it. Logging in has to happen in a terminal because Claude Code's login is interactive.

**Several accounts in one folder.** If you keep one folder (such as `~/.claude`) and switch with `/logout` and `/login`, each session is still counted under the account that was logged in when it **started**:

1. Install both hooks: `claude-worklog install-hook` (or **Install hook** in Settings). The `SessionStart` hook reads only the account id and e-mail from Claude Code's `~/.claude.json` (never credentials) and saves `session → account` in the local state file. It prints nothing.
2. Log in with each account and start at least one session.
3. Open **Settings → Logins seen on this device**. Each login appears with a masked e-mail such as `a***@example.com`. A login marked **No name yet** is counted under the folder's default account. Type a name and press **Name this login**.

This writes an entry like the following to `config.toml` (you can also write it by hand):

```toml
[[accounts]]
name = "personal"            # no match_*: the folder's default
config_dir = "~/.claude"

[[accounts]]
name = "work"
config_dir = "~/.claude"
match_account_id = "…"       # or: match_email = "you@example.com"
```

- Sessions match by account id, then e-mail (case-insensitive). Anything else, including sessions from before the hook was installed, goes to the folder's **default** account (the one without `match_*`).
- Naming (or renaming) a login moves its sessions from the last 30 days to that account straight away. Days whose Claude Code logs are already deleted keep the old account. Only hook-recorded sessions are moved.
- A login that is the only one ever seen in its own folder needs no name: it is simply counted as that folder's account.
- Tracking is per session. If you `/login` to another account *inside* a running session, the session stays with the account it started with. Start a new session after switching.
- Summaries run `claude -p` with whichever account is logged in to the summarizer account's folder.

## claude.ai chats

Claude Code is collected automatically. claude.ai chats (web, desktop and mobile apps) aren't stored on your computer and have no API, so they come from claude.ai's **data export** instead. Open the **Sources** page in the web UI for all of this.

1. In claude.ai, open **Settings → Privacy → Export data**.
2. Wait for the e-mail and open its link. It downloads a small JSON file that lists several downloads.
3. Download **`conversations-000.zip`** (and `conversations-001.zip`… if your export has more parts). You don't need the other files (`memories`, `light_metadata`, `design_chats`, `frames`). The links expire after 24 hours and may only work once.
4. Import it in one of three ways:
   - **Upload** it on the Sources page, or
   - turn on **Import from Downloads** on the Sources page: every sync (after each Claude Code session, every hour, and when you press Sync now) imports new `conversations-*.zip` files it finds in your Downloads folder, or
   - run `claude-worklog import-chats ~/Downloads/conversations-000.zip` (a `conversations.json` or a folder holding several parts works too; `--account work` files them under another account and remembers that for this claude.ai login; `--since 2026-09-01` skips older messages).

Chats show up on the day they happened (not on the day you import them), under **claude.ai chat**, or under the project folder when Claude worked on files in its own sandbox (`/home/claude/<project>`). Each chat is listed with its title and time, and a chat that spans several days is split by your local date. Days with chats have a dot under their bar in the 14-day chart, the Sources page lists them, and an empty day links to your latest chats.

**What each chat shows.** By default: its title (or your first message, for untitled chats) and your first few messages. Turn on **Also keep claude.ai's own summary of each chat** on the Sources page and re-import, and each chat also shows the short summary claude.ai wrote for it, which usually says best what you did. **Write summaries** on a day turns all of it into a few written bullets.

**Things to know**
- **No token counts.** The export doesn't include usage, so chats show what you did, not tokens.
- **Importing again is safe.** Unchanged chats are skipped, and an older export never overwrites a newer one. Request a new export whenever you want to catch up.
- **No double counting.** A chat that is really a Claude Code session already recorded from this device's transcripts (matched by its tool-call ids) is skipped. The same export imported on two devices is counted once.
- **Work accounts.** On Team and Enterprise plans, the export may only be available to the organization's admins.
- **Downloads folder.** The toggle is off by default, so nothing there is read unless you turn it on. Only files named `conversations*.zip` / `conversations*.json` are opened, their content is checked (a ChatGPT export, which also has a `conversations.json`, is refused), half-finished downloads are skipped, and files are never moved or deleted. The folder comes from `XDG_DOWNLOAD_DIR` (usually `~/Downloads`) unless you set another one.

The settings live in the `[chat]` section of the config file (`account`, `watch_downloads`, `downloads_dir`, `keep_summaries`).

## Account switching and new folders

Claude Code's own log files don't say which account ran a session. Worklog learns it two ways:

1. **The hook** (most precise): at the start of every session it notes who is logged in.
2. **The watcher**: while Worklog runs, it checks each Claude folder's account file every few seconds and keeps a timeline of logins. A session without a hook record gets the login that was active when it started. It works with `/login` and with any one-click account-switching app, because every switch has to change that file.

Each record says how its account was found (`account_source`: `hook`, `watcher`, `folder` for a folder with only one account, or `default` when nobody knows).

The watcher also looks for **Claude folders you haven't added yet** (such as `~/.claude-client`, or folders an account-switching app keeps under `~/.config`, `~/Library/Application Support` or `%APPDATA%`) and for **hooks another tool removed**. They show as a notice at the top of the page with a one-click fix: **Name it**, **Add as account** or **Reinstall hook**. Each notice can be dismissed.

Turn on **Settings → Set up new accounts automatically** (`[watch] auto_setup = true`) and Worklog adds new folders as accounts and reinstalls missing hooks by itself, then leaves a note saying what it did.

Only the account id and e-mail are read from the account file, as the hook does. Credentials are never opened. The timeline stays in `~/.local/state/claude-worklog/watch.json` on this device (mode 600).

## The dashboard and Fun mode

The day page shows your tokens, sessions, projects and time, a 14-day chart, **When you worked** (a 24-hour clock), the **last 5 weeks** with your streak, **Projects today**, and a donut of the models Claude Code used. Everything animates in gently. If your system asks apps to reduce motion, Worklog doesn't animate at all.

**Fun mode** (on by default; Settings → *Fun mode*, or `[ui] fun = false`) adds a streak flame, a daily fun fact, a sleepy robot on empty days, and confetti for a record day (your biggest in 5 weeks), streak milestones and finished summaries, each only once. There's also a hidden party mode. (Hint: try typing `party` on the day page. Esc stops it.)

## Command line

The UI covers everyday use. Everything is also available from the terminal:

```bash
claude-worklog demo                            # try the UI on fake data (port 8766, nothing touched)
claude-worklog doctor                          # check config, data repo, accounts, hooks and models
claude-worklog report --stdout                 # today's report in the terminal
claude-worklog report --date 2026-09-25        # write reports/2026-09-25/<device>.md to the data repo
claude-worklog report --no-ai                  # plain prompt lists, no claude -p
claude-worklog collect --days 7                # backfill a week from this machine
claude-worklog import-chats conversations-000.zip [--account NAME] [--since DATE]  # claude.ai export
claude-worklog install-hook [--account NAME]   # add the Claude Code hooks (backs up settings.json)
claude-worklog install-service [--no-report] [--port N]   # start at login (systemd, launchd, Task Scheduler)
claude-worklog uninstall-service              # stop starting at login (keeps your data)
claude-worklog serve --port 8765               # run the UI in the foreground
systemctl --user status claude-worklog-web     # service state
journalctl --user -u claude-worklog-web -f     # live logs
```

Data repo layout (every path is written by a single device, so there are no merge conflicts):

```
records/<date>/<device>/<account>/<session>.json
records/<date>/<device>/<account>/chat-<chat id>.json   # imported claude.ai chats
reports/<date>/<device>.md
```

## Privacy

This tool reads your Claude Code transcripts, so it is built to keep as little as possible.

**What is stored.** One JSON record per session per day, in your data repo:
- date, start and end time, account **name** and device **name** (the names you choose),
- project name, git remote as `host/owner/repo` (credentials always removed), branch names, and the project path relative to your home folder,
- token counts per model, number of API calls and tool calls,
- your first 30 prompts in the session, **cut to 300 characters and redacted**,
- paths of files Claude edited (relative to the repo, or to your home folder outside a repo), and the short hash and redacted subject of **your own** commits that day.

**Imported claude.ai chats.** One record per chat per day: the chat title (redacted, at most 120 characters), your own messages (first 30, **cut to 300 characters and redacted**), the number of messages, tool names, and names of files Claude edited in its sandbox. Only if you turn on `keep_summaries`: claude.ai's own summary of the chat (written by Claude), Markdown removed, redacted and cut to 600 characters; turning it off again removes them from this device's records. The export's account id is turned into a short one-way hash in a local file (`~/.local/state/claude-worklog/chats.json`, mode 600) to remember which worklog account it belongs to, and is never written to the data repo. worklog only opens `conversations.json`; the export's other files, including your login history, are never read.

**What is never stored.** File contents, Claude's replies, tool output, command output, your git e-mail, credentials. From claude.ai exports also: Claude's thinking, chat summaries (unless you turn on `keep_summaries`), attachments and their extracted text. Account ids and login e-mails (used to tell accounts apart) stay in a local file (`~/.local/state/claude-worklog/accounts.json`, mode 600) and are never written to the data repo.

**What is redacted.** Before anything is written, prompts and commit subjects are scrubbed of common API keys (Anthropic, OpenAI, GitHub, GitLab, Bitbucket, AWS, Slack, Google), JWTs, bearer tokens, private keys, `password=…`-style pairs, credentials in URLs, e-mail addresses and IPv4 addresses. This is **best effort**, which is why the data repo **must be private**.

**Where data goes.** Records stay on your machine and in the git remote you configure for the data repo, and nowhere else. There is no telemetry and no server run by this project. One exception you control: **AI summaries** (on by default, `use_ai = true`) run your own `claude -p` once per project per day. That sends that project's redacted prompts, edited file names and commit subjects to Anthropic under your Claude account, the same way any Claude Code session does, and uses tokens. Set `use_ai = false` (or untick it in Settings) to keep summaries fully local as plain prompt lists.

**The web UI** listens on 127.0.0.1 and ::1 only. It rejects requests with a non-loopback `Host` header (which blocks DNS rebinding), requires a CSRF token and a same-origin `Origin` on every form, loads nothing external, and sends a strict Content-Security-Policy. See [SECURITY.md](../SECURITY.md) to report problems.

## Limitations

- **claude.ai chats need an export.** They aren't written to local transcripts, so they're imported from claude.ai's data export by hand or from your Downloads folder, not live, and without token counts. See [claude.ai chats](#claudeai-chats).
- **The transcript format is unofficial.** Claude Code's `~/.claude/projects/**/*.jsonl` files are not a documented, stable API and may change in any update. The parser is defensive: lines it doesn't understand are skipped and counted, never fatal. Still, after a Claude Code upgrade it's worth comparing totals with another tool such as `npx ccusage daily`.
- **macOS and Windows support is new (0.4.0).** The automated tests run on all three systems, but Linux has had the most real-world use. Please report anything odd. On WSL, Worklog runs as on Linux and needs systemd turned on in WSL.
- **The watcher only sees what happens while Worklog runs.** Sessions from before it noticed a login, from switches in the same few seconds as a session start, or from a tool that changes only stored credentials without updating Claude Code's account file, go to the folder's default account. The hook doesn't have the first two gaps.
- **Tokens are not cost.** On Pro/Max plans, usage counts against limits rather than being billed, so no dollar figures are shown.
- **Days use each device's local time.**
- **Retention.** Claude Code deletes old transcripts after `cleanupPeriodDays` (in `settings.json`). Collection runs at least hourly, so this only matters for backfilling with `collect --days N` (up to 30).

## Uninstall

The same two commands on every system:

```bash
claude-worklog uninstall-service      # stops starting at login and removes the scheduled jobs
pipx uninstall worklog-for-claude-code
```

On Windows, a web UI that is still running stops at your next log out, or end `pythonw.exe` in Task Manager.

Then remove the `SessionStart` and `SessionEnd` entries that run `claude-worklog hook` from each account's `settings.json` (a backup from before the install sits next to it as `settings.json.bak-*`). Optionally delete `~/.config/claude-worklog/` and `~/.local/state/claude-worklog/`. Your data repo is left untouched. Delete it yourself if you no longer want it.
